pipeline {
  agent { label 'linux-arm64' }
  environment {
    PYPI_INDEX_URL = credentials('artifactory-pypi-url')
    ARTIFACTORY_KIRO_CLI_URL = credentials('artifactory-kiro-cli-arm64-tgz')
  }
  stages {
    stage('Setup') {
      steps { sh 'make setup' }
    }
    stage('Verify') {
      steps {
        sh 'make lint'
        sh 'make test'
      }
    }
    stage('Image') {
      steps {
        sh '''
          docker build --platform linux/arm64 \
            --build-arg PYPI_INDEX_URL="$PYPI_INDEX_URL" \
            --build-arg ARTIFACTORY_KIRO_CLI_URL="$ARTIFACTORY_KIRO_CLI_URL" \
            -t "${ECR_REPO}:${BUILD_NUMBER}" agent/
        '''
      }
    }
    stage('Deploy') {
      when { branch 'main' }
      steps {
        sh 'make deploy EMAIL="$BUDGET_ALERT_EMAIL"'
      }
    }
  }
}