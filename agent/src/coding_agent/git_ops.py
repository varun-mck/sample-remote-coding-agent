"""Every git invocation in the container, and the one configuration group they share.

Two facts about this environment shape the whole module:

**The session-storage mount is writable but not owned by this process's user**
(invariant 25), so git refuses every repository on it with `fatal: detected dubious
ownership` -- including one this process created a millisecond earlier. The exemption
is carried in `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_<n>` / `GIT_CONFIG_VALUE_<n>`
rather than in a config file, because that is the only channel reaching **both**
`commands._base_env()` (which builds a command's environment from nothing and copies
the group back explicitly) **and** every coding subagent's inherited `Bash`.

**The GitHub PAT must never enter this process's environment, the `origin` URL or a
command line** (invariant 4), because every role holds `Bash` and inherits what this
process has. Network git therefore goes through `_run_authenticated`, which builds a
fresh minimal environment and points `GIT_ASKPASS` at a script in a 0700 directory
that exists only for the length of the call.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path

log = logging.getLogger(__name__)

# A clone is the long pole here; every other command in this module is sub-second. The
# bound exists so a hung git surfaces as a `GitError` naming the timeout instead of
# consuming the session until AgentCore reaps it with nothing in the log.
_GIT_TIMEOUT = 600

# The commit identity, and it has to be configurable rather than hardcoded, because no
# single value is right everywhere. `agent@localhost` is the default and matches no GitHub
# account, so GitHub resolves *no author* for the commit -- `.author` is `null` in the
# commits API -- and any check that gates on commit attribution then refuses the pull
# request. A Vercel preview reports `Deployment was blocked` and never builds; a repository
# with a DCO or signed-commit policy fails the same way. That failure is expensive to read:
# the pull request goes red with a deployment error and nothing in the diff explains it,
# because the cause is the author of the commit rather than anything in it.
#
# Fleet policy (`provisioner._fleet_environment`), not per-configuration: it is one
# deployment-wide identity, so a runtime provisioned before this existed converges onto it
# on the next update instead of keeping what it was born with (invariant 24).
#
# Deliberately NOT git's own `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL`: those are read directly
# by every git process and would take precedence over this config group, so the two could
# disagree with the environment silently winning. `AGENT_` prefixed, they cannot.
_DEFAULT_AUTHOR_NAME = "agentcore-coding-agent"
_DEFAULT_AUTHOR_EMAIL = "agent@localhost"

# The process-wide config group, in `GIT_CONFIG_KEY_<n>` order. Seeded with an identity
# because `submit` commits and a container has no `~/.gitconfig`, and with
# `advice.detachedHead=false` because that advice is six lines of noise in every log.
# `trust_directory` appends `safe.directory` entries to it; nothing is ever removed.
#
# The two reads are spelled out here rather than pulled into a helper so the variable names
# are *literals* at the read site: `tests/test_env_contract.py`'s scan recovers names
# statically and can only prove a variable is forwarded when it can see one, so a helper
# taking the name as an argument would have to be excused in
# `CONTAINER_DYNAMIC_SITES` instead of checked. `or` on a stripped value because a
# Lambda-forwarded variable arrives as `""` rather than absent when nothing set it, so
# empty has to mean "use the default" and not "commit with an empty identity", which git
# rejects outright.
_CONFIG_ENTRIES: list[tuple[str, str]] = [
    ("user.name", os.environ.get("AGENT_GIT_AUTHOR_NAME", "").strip() or _DEFAULT_AUTHOR_NAME),
    ("user.email", os.environ.get("AGENT_GIT_AUTHOR_EMAIL", "").strip() or _DEFAULT_AUTHOR_EMAIL),
    ("advice.detachedHead", "false"),
]

# git's own minimal environment. Built here and not imported from `commands._base_env()`
# -- that would be a circular import, since `commands` copies `git_config_env()` into
# every repository command's environment. No `AWS_*`, no token, nothing by accident.
_GIT_ENV_PASSTHROUGH = ("PATH", "HOME", "LANG")

# Exactly the script git runs to answer a credential prompt. `$1` is the prompt text,
# so the username branch is matched on it; the token is read from a sibling file rather
# than interpolated here, because this script's contents are world-readable the moment
# anything widens the directory's mode.
_ASKPASS_SCRIPT = """#!/bin/sh
case "$1" in
  Username*) cat "$(dirname "$0")/username" ;;
  *) cat "$(dirname "$0")/token" ;;
esac
"""

# What a repository's own setup and test commands leave behind, as `.gitignore` patterns.
# Written to the clone's `.git/info/exclude` -- local to the clone, never pushed, and never
# an edit to the repository's own `.gitignore` -- so a repository with no `.gitignore`
# would otherwise treat build artefacts as untracked files the agent might commit.
# Tracked files are untouched (an exclude only hides *untracked* paths), so a repository that
# deliberately commits one of these keeps doing so.
BUILD_ARTIFACT_PATTERNS = (
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".venv/",
    "node_modules/",
)


class GitError(RuntimeError):
    """A git command failed, timed out, or ran somewhere unusable.

    The message carries git's own stderr rather than a paraphrase: "detected dubious
    ownership" and "could not read Username" are diagnosable, and anything this module
    invented instead would not be.
    """


def _git(
    args: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> tuple[int, str, str]:
    """Run one git command, returning `(exit_code, stdout, stderr)`.

    `env=None` inherits this process's environment, which is where `_export` put the
    config group. `check=False` is for the commands whose non-zero exit is an answer
    (`rev-parse --verify` on a branch that may not exist) rather than a failure.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {_GIT_TIMEOUT}s") from exc
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
        raise GitError(f"git {' '.join(args)} failed with exit {proc.returncode}: {detail}")
    return proc.returncode, proc.stdout, proc.stderr


def _config_env(entries: Sequence[tuple[str, str]]) -> dict[str, str]:
    """Render config entries as git's environment-variable config form.

    The count and its pairs move as a unit: a `GIT_CONFIG_COUNT` of 4 with three pairs
    is `fatal: missing config key GIT_CONFIG_KEY_3` on *every* subsequent git command,
    which is why no caller ever builds a partial group by hand.
    """
    env = {"GIT_CONFIG_COUNT": str(len(entries))}
    for n, (key, value) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{n}"] = key
        env[f"GIT_CONFIG_VALUE_{n}"] = value
    return env


def git_config_env() -> dict[str, str]:
    """The whole config group as an environment mapping -- never a subset."""
    return _config_env(_CONFIG_ENTRIES)


def _export() -> None:
    """Publish the group into `os.environ`, where subagent `Bash` inherits it.

    Stale higher-numbered keys are deleted rather than left behind: the group only
    grows within one process, but a parent could have exported a longer one, and a
    `KEY_<n>` above the count is at best ignored and at worst confusing to debug.
    """
    os.environ.update(git_config_env())
    n = len(_CONFIG_ENTRIES)
    while f"GIT_CONFIG_KEY_{n}" in os.environ or f"GIT_CONFIG_VALUE_{n}" in os.environ:
        os.environ.pop(f"GIT_CONFIG_KEY_{n}", None)
        os.environ.pop(f"GIT_CONFIG_VALUE_{n}", None)
        n += 1


def trust_directory(path: Path) -> None:
    """Exempt `path` from git's ownership check, by exact name.

    git 2.39 (what this image ships) expands **no glob** in `safe.directory`, so
    `*` is not an option and every path is named in full.

    Both the worktree and its gitdir go in, because git names different ones depending
    on what it is doing: a plain `git status` on a foreign-owned repo reports the
    worktree, while a *clone source* is reported as `<path>/.git`. Adding one and not
    the other produces a failure in exactly one of the two paths, which reads as
    something far more mysterious than it is.
    """
    resolved = path.resolve()
    for candidate in (resolved, resolved / ".git"):
        entry = ("safe.directory", str(candidate))
        if entry not in _CONFIG_ENTRIES:
            _CONFIG_ENTRIES.append(entry)
    _export()


# Once at import, so the identity and the exemptions are live for anything that runs
# git -- this module, `commands`, or a subagent's `Bash` -- without a caller having to
# remember to prime them.
_export()


def probe_repository(root: Path) -> None:
    """Prove git can actually operate under `root`, with a throwaway repository.

    `mkdir` reporting success proves nothing about the mount: on a filesystem whose
    ownership the container does not control, git raises `fatal: detected dubious
    ownership` on a directory created moments earlier. A real `git init` plus `git
    status` catches that before the clone starts, and raises `GitError` carrying git's
    stderr.
    """
    probe = root / f".probe-{os.getpid()}"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        # The probe is a *child* of an already-trusted root, and with no glob in
        # `safe.directory` a trusted parent covers nothing below it. Its two paths go
        # into a throwaway copy of the group rather than the process-wide one, since
        # the directory is deleted a few lines below and an entry for a path that no
        # longer exists is permanent noise in every later git command.
        resolved = probe.resolve()
        env = dict(os.environ) | _config_env(
            [
                *_CONFIG_ENTRIES,
                ("safe.directory", str(resolved)),
                ("safe.directory", str(resolved / ".git")),
            ]
        )
        _git(["init", "--quiet"], cwd=probe, env=env)
        _git(["status", "--porcelain"], cwd=probe, env=env)
    except OSError as exc:
        raise GitError(f"{root} is not usable as a work root: {exc}") from exc
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def _is_local(source: str) -> bool:
    """True for a filesystem path or a `file://` URL: there is nothing to authenticate."""
    if source.startswith("file://"):
        return True
    # A scheme, or an scp-style `git@host:owner/repo`, means a network remote.
    return "://" not in source and not source.startswith("git@")


def clone(source: str, dest: Path, *, token: str | None = None, branch: str | None = None) -> None:
    """Clone `source` into `dest`, authenticating only when the source is remote."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--quiet"]
    if branch:
        args.extend(["--branch", branch])
    args.extend([source, str(dest)])
    if _is_local(source):
        # Nothing to authenticate, but the source repository is on the same
        # foreign-owned mount and git reports a clone source as its *gitdir*, so it
        # needs the exemption too -- no caller above here knows the source path.
        trust_directory(Path(source.removeprefix("file://")))
        _git(args, cwd=dest.parent)
        return
    _run_authenticated(args, cwd=dest.parent, token=token)


def checkout_branch(repo: Path, branch: str, *, token: str | None = None) -> None:
    """Check out an existing branch from `origin` (never creates a new branch name)."""
    fetch_args = [
        "fetch",
        "--quiet",
        "origin",
        f"refs/heads/{branch}:refs/remotes/origin/{branch}",
    ]
    if token:
        _run_authenticated(fetch_args, cwd=repo, token=token)
    else:
        _git(fetch_args, cwd=repo)
    code, _, _ = _git(
        ["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
        cwd=repo,
        check=False,
    )
    if code != 0:
        raise GitError(
            f"branch {branch!r} was not found on origin after clone; "
            "create the branch on the remote before submitting a task"
        )
    _git(["checkout", "-B", branch, f"origin/{branch}"], cwd=repo)


def exclude_build_artifacts(repo: Path) -> None:
    """Add `BUILD_ARTIFACT_PATTERNS` to the clone's `.git/info/exclude`, once each.

    Idempotent, because a resumed task re-runs `checkout` against the same clone.
    """
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    present = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    missing = [pattern for pattern in BUILD_ARTIFACT_PATTERNS if pattern not in present]
    if not missing:
        return
    lines = [*present, "# build artefacts the agent never commits (git_ops)", *missing]
    exclude.write_text("\n".join(lines) + "\n", encoding="utf-8")


def current_branch(repo: Path) -> str:
    """The checked-out branch name (`HEAD` when detached)."""
    _, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    return out.strip()


@contextlib.contextmanager
def _askpass(token: str, *, username: str = "x-access-token") -> Iterator[Path]:
    """Yield the path of a throwaway `GIT_ASKPASS` script that prints `token`.

    A 0700 directory holding a 0600 `token` file and the script that cats it, removed
    in `finally`. This is the only place the PAT touches anything durable, and it
    reaches git without appearing on a command line (readable through `/proc`) or in
    this process's environment (inherited by every subagent's `Bash`).
    """
    tmp = Path(tempfile.mkdtemp(prefix="askpass-"))
    try:
        tmp.chmod(0o700)
        token_file = tmp / "token"
        token_file.write_text(token, encoding="utf-8")
        token_file.chmod(0o600)
        user_file = tmp / "username"
        user_file.write_text(username, encoding="utf-8")
        user_file.chmod(0o600)
        script = tmp / "askpass.sh"
        script.write_text(_ASKPASS_SCRIPT, encoding="utf-8")
        script.chmod(0o700)
        yield script
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _base_env_for_git() -> dict[str, str]:
    """`PATH`, `HOME`, `LANG` when set -- and deliberately nothing else."""
    return {name: os.environ[name] for name in _GIT_ENV_PASSTHROUGH if name in os.environ}


def _run_authenticated(
    args: Sequence[str], *, cwd: Path, token: str | None, username: str = "x-access-token"
) -> None:
    """Run a network git command with credentials supplied out of band.

    The environment is built from nothing rather than copied, so the token is the only
    secret anywhere near it and `AWS_*` is absent by construction.
    `GIT_TERMINAL_PROMPT=0` because there is no terminal to answer a prompt and a
    blocked git looks exactly like a slow clone until the session is reaped.
    """
    env = _base_env_for_git() | git_config_env()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token is None:
        # A public clone needs no credentials, so `clone` has no special case: it
        # passes on whatever token it was given, including none.
        _git(list(args), cwd=cwd, env=env)
        return
    with _askpass(token, username=username) as script:
        env["GIT_ASKPASS"] = str(script)
        _git(list(args), cwd=cwd, env=env)
