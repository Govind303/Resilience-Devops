
pipeline {
    agent {
        label 'wsl-linux'
    }

    options {
        timestamps()
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
        timeout(time: 45, unit: 'MINUTES')
    }

    parameters {
        string(
            name: 'STAGING_HOST',
            defaultValue: '192.168.56.10',
            description: 'Staging VM address'
        )
        string(
            name: 'STAGING_USER',
            defaultValue: 'vagrant',
            description: 'SSH user on the staging VM'
        )
        booleanParam(
            name: 'SELF_HEALING_ENABLED',
            defaultValue: true,
            description: 'Untick to deploy a fragile build and test the chaos gate'
        )
        booleanParam(
            name: 'PROMOTE_TO_PRODUCTION',
            defaultValue: true,
            description: 'Enable local release-promotion simulation after the gate passes'
        )
    }

    environment {
        APP_VERSION = "1.0.${env.BUILD_NUMBER}"
        STAGING_URL = "http://${params.STAGING_HOST}:8080"
        ANSIBLE_CONFIG = "${env.WORKSPACE}/ansible/ansible.cfg"
        ANSIBLE_FORCE_COLOR = 'true'
        STAGING_DEPLOYED = 'false'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                script {
                    env.GIT_COMMIT = sh(
                        script: 'git rev-parse HEAD',
                        returnStdout: true
                    ).trim()
                }
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
                    junit allowEmptyResults: true,
                          testResults: 'app/target/surefire-reports/*.xml'
                }
                success {
                    archiveArtifacts(
                        artifacts: 'app/target/resilienceops-app.jar',
                        fingerprint: true
                    )
                }
            }
        }

        stage('Provision Staging (Ansible)') {
            steps {
                sshagent(credentials: ['vagrant']) {
                    sh '''
                        ansible-playbook \
                            -i ansible/inventories/staging.yml \
                            ansible/provision.yml
                    '''
                }
            }
        }

        stage('Deploy to Staging (Ansible)') {
            steps {
                sshagent(credentials: ['vagrant']) {
                    sh '''
                        ansible-playbook \
                            -i ansible/inventories/staging.yml \
                            ansible/deploy_staging.yml \
                            -e self_healing_enabled=${SELF_HEALING_ENABLED}
                    '''
                }
            }
            post {
                success {
                    script {
                        env.STAGING_DEPLOYED = 'true'
                    }
                }
            }
        }

        stage('Chaos Engineering Gate') {
            steps {
                sshagent(credentials: ['vagrant']) {
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
                    junit allowEmptyResults: true,
                          testResults: 'reports/chaos/TEST-*.xml'
                    archiveArtifacts(
                        allowEmptyArchive: true,
                        artifacts: 'reports/chaos/**'
                    )
                }
            }
        }

        stage('Mark Build Known-Good') {
            steps {
                sshagent(credentials: ['vagrant']) {
                    sh '''
                        ansible-playbook \
                            -i ansible/inventories/staging.yml \
                            ansible/mark_good.yml
                    '''
                }
            }
        }

        stage('Promote: Local Release') {
            when {
                expression { params.PROMOTE_TO_PRODUCTION }
            }
            steps {
                script {
                    sh '''
                        mkdir -p reports/releases

                        cat > "reports/releases/release-${BUILD_NUMBER}.txt" <<EOF
ResilienceOps Local Release Record
Build: ${BUILD_NUMBER}
Application version: ${APP_VERSION}
Git commit: ${GIT_COMMIT}
Chaos gate: PASSED
Known-good status: RECORDED
Promotion mode: LOCAL SIMULATION
EOF
                    '''

                    archiveArtifacts(
                        artifacts: "reports/releases/release-${env.BUILD_NUMBER}.txt",
                        fingerprint: true
                    )

                    echo "Local promotion simulation completed for ${env.APP_VERSION}"
                }
            }
        }
    }

    post {
        failure {
            script {
                if (env.STAGING_DEPLOYED == 'true') {
                    sshagent(credentials: ['vagrant']) {
                        sh '''
                            ansible-playbook \
                                -i ansible/inventories/staging.yml \
                                ansible/rollback_staging.yml || true
                        '''
                    }
                }
            }

            sh '''
                python3 scripts/alert.py \
                    --severity critical \
                    --title "ResilienceOps build ${APP_VERSION} blocked" \
                    --message "The pipeline failed. Promotion was blocked; staging rollback was attempted when deployment had completed." \
                    --link "$BUILD_URL"
            '''
        }

        success {
            sh '''
                python3 scripts/alert.py \
                    --severity info \
                    --title "ResilienceOps build ${APP_VERSION} passed the chaos gate" \
                    --message "All resilience experiments passed. The known-good release was recorded and the local promotion stage completed or was disabled." \
                    --link "$BUILD_URL"
            '''
        }

        always {
            echo "ResilienceOps pipeline finished with status: ${currentBuild.currentResult}"
        }
    }
}
