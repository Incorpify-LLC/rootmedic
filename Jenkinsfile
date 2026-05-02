pipeline {
    agent any

    environment {
        ANSIBLE_HOST_KEY_CHECKING = 'False'
        INVENTORY_FILE = '/tmp/rootmedic-ci-inventory.ini'
        VM1_IP = '192.168.122.101'
        VM2_IP = '192.168.122.102'
    }

    parameters {
        booleanParam(name: 'KEEP_VMS', defaultValue: false,
                     description: 'Keep VMs running after pipeline completes')
        booleanParam(name: 'SKIP_PROVISION', defaultValue: false,
                     description: 'Skip VM provisioning (use existing VMs)')
    }

    stages {

        // ============================================================
        stage('Checkout') {
            steps {
                checkout scm
                sh 'echo "Repository cloned at ${WORKSPACE}"'
            }
        }

        // ============================================================
        stage('Install CI Dependencies') {
            steps {
                sh '''
                    pip install ansible pytest requests --quiet
                    ansible --version
                    pytest --version
                '''
            }
        }

        // ============================================================
        stage('Unit Tests') {
            steps {
                sh '''
                    cd ${WORKSPACE}
                    python -m pytest tests/ -v --tb=short --junitxml=junit-results.xml
                '''
            }
            post {
                always {
                    junit 'junit-results.xml'
                }
            }
        }

        // ============================================================
        stage('Provision VMs') {
            when { expression { !params.SKIP_PROVISION } }
            steps {
                sh '''
                    ansible-playbook -i localhost, \
                        ${WORKSPACE}/ci/provision-vms.yml \
                        --connection=local
                '''
            }
        }

        // ============================================================
        stage('Deploy Log Aggregation (VM1)') {
            steps {
                sh '''
                    ansible-playbook -i ${INVENTORY_FILE} \
                        ${WORKSPACE}/ci/deploy-logging.yml
                '''
            }
        }

        // ============================================================
        stage('Deploy RootMedic Agent (VM2)') {
            steps {
                sh '''
                    ansible-playbook -i ${INVENTORY_FILE} \
                        ${WORKSPACE}/ci/deploy-rootmedic.yml
                '''
            }
        }

        // ============================================================
        stage('Install Dashboard') {
            steps {
                sh '''
                    if [ -f ${WORKSPACE}/ci/rootmedic-dashboard.json ]; then
                        curl -s -X POST \
                            -H "Content-Type: application/json" \
                            -d @${WORKSPACE}/ci/rootmedic-dashboard.json \
                            "http://admin:admin@${VM1_IP}:3000/api/dashboards/db"
                        echo "  ✓ Dashboard imported to Grafana"
                    fi
                '''
            }
        }

        // ============================================================
        stage('Inject Fault & Verify Recovery') {
            steps {
                sh '''
                    echo "============================================="
                    echo "  RootMedic — Autonomous Healing Demo"
                    echo "  Log Aggregator : http://${VM1_IP}:3000"
                    echo "  Managed Node   : ${VM2_IP}"
                    echo "============================================="

                    ansible-playbook -i ${INVENTORY_FILE} \
                        ${WORKSPACE}/ci/inject-fault.yml
                '''
            }
        }

        // ============================================================
        stage('Collect Remediation Evidence') {
            steps {
                sh '''
                    echo "=== Remediation State ==="
                    ssh -o StrictHostKeyChecking=no rootmedic@${VM2_IP} \
                        "cat /opt/rootmedic/remediation_state.json 2>/dev/null || echo 'No state file'"

                    echo ""
                    echo "=== Dry-Run Log ==="
                    ssh -o StrictHostKeyChecking=no rootmedic@${VM2_IP} \
                        "cat /opt/rootmedic/dry_run.log 2>/dev/null || echo 'No dry-run log'"

                    echo ""
                    echo "=== Loki Query (last 10 error entries) ==="
                    curl -s "${VM1_IP}:3100/loki/api/v1/query_range" \
                        --data-urlencode 'query={job="systemd-journal"}' \
                        --data-urlencode 'limit=10' \
                        | python3 -m json.tool 2>/dev/null || echo 'Loki not reachable'
                '''
            }
        }

    }  // end stages

    // ================================================================
    post {
        success {
            echo '''
                ╔══════════════════════════════════════════════════════╗
                ║  ✅ RootMedic CI/CD Demo PASSED                     ║
                ║  Autonomous healing verified on managed node.       ║
                ║  Grafana dashboards: http://''' + "${VM1_IP}" + ''':3000      ║
                ╚══════════════════════════════════════════════════════╝
            '''
        }
        failure {
            echo '''
                ╔══════════════════════════════════════════════════════╗
                ║  ❌ RootMedic CI/CD Demo FAILED                     ║
                ║  Check logs above for details.                      ║
                ╚══════════════════════════════════════════════════════╝
            '''
        }
        cleanup {
            script {
                if (!params.KEEP_VMS) {
                    sh '''
                        echo "Tearing down VMs..."
                        virsh destroy rootmedic-log-aggregator 2>/dev/null || true
                        virsh undefine --remove-all-storage rootmedic-log-aggregator 2>/dev/null || true
                        virsh destroy rootmedic-managed-node 2>/dev/null || true
                        virsh undefine --remove-all-storage rootmedic-managed-node 2>/dev/null || true
                        echo "VMs destroyed."
                    '''
                } else {
                    echo "Keeping VMs as requested (KEEP_VMS=true)."
                    echo "Log Aggregator: http://${VM1_IP}:3000"
                    echo "Managed Node:   ssh rootmedic@${VM2_IP}"
                }
            }
        }
    }
}
