// ResilienceOps CI pipeline: build -> test -> provision staging -> deploy -> chaos gate -> promote.
// A build reaches production only if it survives the chaos experiments on staging.
//
// Agent needs: JDK 17, Maven, Python 3, Ansible, and SSH access to the staging VM.
// Jenkins credentials: 'staging-ssh' (SSH private key for the staging VM user)
//                      'azure-devops-pat' (secret text, Azure DevOps personal access token)
// Optional global env vars: ALERT_WEBHOOK_URL, AZDO_ORG, AZDO_PROJECT, AZDO_PIPELINE_ID
pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
        timeout(time: 45, unit: 'MINUTES')
    }

    parameters {
        string(name: 'STAGING_HOST', defaultValue: '192.168.56.10', description: 'Staging VM address')
        string(name: 'STAGING_USER', defaultValue: 'vagrant', description: 'SSH user on the staging VM')
        booleanParam(name: 'SELF_HEALING_ENABLED', defaultValue: true,
                     description: 'Untick to deploy a fragile build and watch the chaos gate block it')
        booleanParam(name: 'PROMOTE_TO_PRODUCTION', defaultValue: true,
                     description: 'Start the Azure Pipelines blue-green release when the gate passes')
    }

    environment {
        APP_VERSION = "1.0.${env.BUILD_NUMBER}"
        STAGING_URL = "http://${params.STAGING_HOST}:8080"
        ANSIBLE_CONFIG = "${env.WORKSPACE}/ansible/ansible.cfg"
        ANSIBLE_FORCE_COLOR = 'true'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build & Unit Test') {
            steps {
                dir('app') {
                    sh 'mvn -B clean verify'
                }
            }
            post {
                always {
                    junit 'app/target/surefire-reports/*.xml'
                }
                success {
                    archiveArtifacts artifacts: 'app/target/resilienceops-app.jar', fingerprint: true
                }
            }
        }

        stage('Provision Staging (Ansible)') {
            steps {
                sshagent(credentials: ['staging-ssh']) {
                    sh 'ansible-playbook -i ansible/inventories/staging.yml ansible/provision.yml'
                }
            }
        }

        stage('Deploy to Staging (Ansible)') {
            steps {
                script { env.STAGING_DEPLOYED = 'true' }
                sshagent(credentials: ['staging-ssh']) {
                    sh """
                        ansible-playbook -i ansible/inventories/staging.yml ansible/deploy_staging.yml \
                            -e self_healing_enabled=${params.SELF_HEALING_ENABLED}
                    """
                }
            }
        }

        stage('Chaos Engineering Gate') {
            steps {
                sshagent(credentials: ['staging-ssh']) {
                    sh '''
                        python3 monitoring/resilience_gate.py \
                            --config monitoring/experiments.json \
                            --base-url "$STAGING_URL" \
                            --inventory ansible/inventories/staging.yml \
                            --report-dir reports/chaos
                    '''
                }
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'reports/chaos/TEST-*.xml'
                    archiveArtifacts allowEmptyArchive: true, artifacts: 'reports/chaos/**'
                }
            }
        }

        stage('Mark Build Known-Good') {
            steps {
                sshagent(credentials: ['staging-ssh']) {
                    sh 'ansible-playbook -i ansible/inventories/staging.yml ansible/mark_good.yml'
                }
            }
        }

        stage('Promote: Azure Pipelines Blue-Green') {
            when {
                expression { params.PROMOTE_TO_PRODUCTION }
            }
            steps {
                withCredentials([string(credentialsId: 'azure-devops-pat', variable: 'AZDO_PAT')]) {
                    sh 'python3 scripts/trigger_azure_release.py --version "$APP_VERSION" --commit "$GIT_COMMIT"'
                }
            }
        }
    }

    post {
        failure {
            script {
                if (env.STAGING_DEPLOYED == 'true') {
                    sshagent(credentials: ['staging-ssh']) {
                        sh 'ansible-playbook -i ansible/inventories/staging.yml ansible/rollback_staging.yml || true'
                    }
                }
            }
            sh '''
                python3 scripts/alert.py --severity critical \
                    --title "ResilienceOps build $APP_VERSION blocked" \
                    --message "The build failed or did not survive the chaos gate. Promotion blocked; staging rolled back to the last known-good build." \
                    --link "$BUILD_URL"
            '''
        }
        success {
            sh '''
                python3 scripts/alert.py --severity info \
                    --title "ResilienceOps build $APP_VERSION passed the chaos gate" \
                    --message "All resilience experiments passed; release handed to Azure Pipelines." \
                    --link "$BUILD_URL"
            '''
        }
    }
}
