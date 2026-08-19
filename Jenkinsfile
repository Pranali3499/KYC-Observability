// =============================================================================
// Jenkinsfile -- Declarative CI/CD & Continuous Training (CT) Pipeline
// KYC Behavioral Observability Framework for Early Risk Assessment in Onboarding
// Student: Pranali Pandharinath Supekar (2024DA04387) | M.Tech DSE, BITS Pilani
// =============================================================================

pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '15', artifactNumToKeepStr: '10'))
        disableConcurrentBuilds()
    }

    parameters {
        choice(
            name: 'PIPELINE_MODE',
            choices: ['FAST_DEMO', 'FULL_PRODUCTION', 'CI_UNIT_ONLY'],
            description: 'Execution mode: FAST_DEMO (~2-3 mins for viva demo), FULL_PRODUCTION (1M rows full evaluation), or CI_UNIT_ONLY'
        )
        booleanParam(
            name: 'SIMULATE_DRIFT_AND_RETRAIN',
            defaultValue: true,
            description: 'Simulate feature/concept drift and trigger automated model retraining'
        )
        booleanParam(
            name: 'CANARY_ROLLOUT',
            defaultValue: true,
            description: 'Execute progressive canary deployment (10% -> 50% -> 100%) with automated health checks'
        )
    }

    environment {
        PYTHONUNBUFFERED = '1'
        PROJECT_DIR      = "${WORKSPACE}"
        REPORTS_DIR      = "${WORKSPACE}/test-reports"
        ARTIFACTS_DIR    = "${WORKSPACE}/pipeline-artifacts"
        KYC_DB_HOST      = "kyc-postgres"
        KAFKA_BOOTSTRAP_SERVERS = "kyc-kafka:29092"
    }

    stages {

        // ---------------------------------------------------------------------
        // Stage 0: Infrastructure & Pre-Flight Environment Verification
        // ---------------------------------------------------------------------
        stage('Stage 0: Infrastructure & Environment Check') {
            steps {
                echo '=================================================================='
                echo '>>> STAGE 0: INFRASTRUCTURE & ENVIRONMENT VERIFICATION <<<'
                echo '=================================================================='
                script {
                    if (isUnix()) {
                        sh '''
                            python3 --version || python --version
                            docker compose version 2>/dev/null || docker-compose version 2>/dev/null || true
                            docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" || true
                            
                            # Symlink dataset & model artifacts from mounted host directory if available
                            if [ -d "/workspace/kyc-observability" ]; then
                                ln -sf /workspace/kyc-observability/*.csv . 2>/dev/null || true
                                ln -sf /workspace/kyc-observability/*.pkl . 2>/dev/null || true
                                ln -sf /workspace/kyc-observability/synthetic_id_documents . 2>/dev/null || true
                                ln -sf /workspace/kyc-observability/biometric_parquet . 2>/dev/null || true
                            fi
                        '''
                    } else {
                        bat '''
                            python --version
                            docker compose up -d || true
                            docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" || true
                        '''
                    }
                }
            }
        }

        // ---------------------------------------------------------------------
        // Stage 1: Automated Test Pyramid Verification (Unit, Regression, E2E)
        // ---------------------------------------------------------------------
        stage('Stage 1: Automated Test Pyramid (55 Tests)') {
            steps {
                echo '=================================================================='
                echo '>>> STAGE 1: AUTOMATED TEST PYRAMID VERIFICATION <<<'
                echo '=================================================================='
                script {
                    if (isUnix()) {
                        sh '''
                            mkdir -p test-reports
                            pytest tests/ -v --junitxml=test-reports/pytest-results.xml --tb=short
                        '''
                    } else {
                        bat '''
                            if not exist test-reports mkdir test-reports
                            python -m pytest tests/ -v --junitxml=test-reports/pytest-results.xml --tb=short
                        '''
                    }
                }
            }
        }

        // ---------------------------------------------------------------------
        // Stage 2: Pre-Ingestion Data Validation & Deduplication Gate
        // ---------------------------------------------------------------------
        stage('Stage 2: Pre-Ingestion Data Validation Gate') {
            when {
                expression { params.PIPELINE_MODE != 'CI_UNIT_ONLY' }
            }
            steps {
                echo '=================================================================='
                echo '>>> STAGE 2: PRE-INGESTION DATA VALIDATION & DEDUPLICATION GATE <<<'
                echo '=================================================================='
                script {
                    def sampleSize = (params.PIPELINE_MODE == 'FAST_DEMO') ? '25000' : '50000'
                    def cmd = "python pre_ingestion_validator.py --csv Base.csv --sample-size ${sampleSize}"
                    if (isUnix()) { sh cmd } else { bat cmd }
                }
            }
        }

        // ---------------------------------------------------------------------
        // Stage 3: Behavioral Feature Engineering & Data Quality Checks
        // ---------------------------------------------------------------------
        stage('Stage 3: Feature Engineering & Data Quality Gate') {
            when {
                expression { params.PIPELINE_MODE != 'CI_UNIT_ONLY' }
            }
            steps {
                echo '=================================================================='
                echo '>>> STAGE 3: BEHAVIORAL FEATURE ENGINEERING & DATA QUALITY <<<'
                echo '=================================================================='
                script {
                    if (isUnix()) {
                        sh '''
                            python feature_engineering.py
                            python data_quality_checks.py
                        '''
                    } else {
                        bat '''
                            python feature_engineering.py
                            python data_quality_checks.py
                        '''
                    }
                }
            }
        }

        // ---------------------------------------------------------------------
        // Stage 4: Cross-Dataset Generalization Evaluation (Base + Variants I-V)
        // ---------------------------------------------------------------------
        stage('Stage 4: Cross-Dataset Model Generalization') {
            when {
                expression { params.PIPELINE_MODE != 'CI_UNIT_ONLY' }
            }
            steps {
                echo '=================================================================='
                echo '>>> STAGE 4: CROSS-DATASET GENERALIZATION EVALUATION <<<'
                echo '=================================================================='
                script {
                    def sampleSize = (params.PIPELINE_MODE == 'FAST_DEMO') ? '10000' : '50000'
                    def cmd = "python cross_dataset_evaluation.py --sample-size ${sampleSize}"
                    if (isUnix()) { sh cmd } else { bat cmd }
                }
            }
        }

        // ---------------------------------------------------------------------
        // Stage 5: Explainability & Actionable Recourse (SHAP + Counterfactuals)
        // ---------------------------------------------------------------------
        stage('Stage 5: SHAP Explainability & Counterfactual Recourse') {
            when {
                expression { params.PIPELINE_MODE != 'CI_UNIT_ONLY' }
            }
            steps {
                echo '=================================================================='
                echo '>>> STAGE 5: SHAP EXPLAINABILITY & COUNTERFACTUAL RECOURSE <<<'
                echo '=================================================================='
                script {
                    def shapSamples = (params.PIPELINE_MODE == 'FAST_DEMO') ? '500' : '2000'
                    def cfSamples   = (params.PIPELINE_MODE == 'FAST_DEMO') ? '100' : '500'
                    if (isUnix()) {
                        sh """
                            python shap_explainability.py --n-samples ${shapSamples}
                            python counterfactual_analysis.py --n-samples ${cfSamples}
                        """
                    } else {
                        bat """
                            python shap_explainability.py --n-samples ${shapSamples}
                            python counterfactual_analysis.py --n-samples ${cfSamples}
                        """
                    }
                }
            }
        }

        // ---------------------------------------------------------------------
        // Stage 6: Biometric Validation, Parquet ETL & Go/No-Go Decision Gate
        // ---------------------------------------------------------------------
        stage('Stage 6: Biometric Sub-components & Decision Gate') {
            when {
                expression { params.PIPELINE_MODE != 'CI_UNIT_ONLY' }
            }
            steps {
                echo '=================================================================='
                echo '>>> STAGE 6: BIOMETRIC VALIDATION, PARQUET ETL & GO/NO-GO GATE <<<'
                echo '=================================================================='
                script {
                    if (isUnix()) {
                        sh '''
                            python biometric_face_matching.py
                            python biometric_liveness_detection.py
                            python document_ocr.py
                            python identity_mismatch_detection.py
                            python biometric_etl_normalize.py
                            python biometric_etl_combine.py
                            python verify_biometric_go_no_go.py
                        '''
                    } else {
                        bat '''
                            python biometric_face_matching.py
                            python biometric_liveness_detection.py
                            python document_ocr.py
                            python identity_mismatch_detection.py
                            python biometric_etl_normalize.py
                            python biometric_etl_combine.py
                            python verify_biometric_go_no_go.py
                        '''
                    }
                }
            }
        }

        // ---------------------------------------------------------------------
        // Stage 7: Real-Time Kafka Streaming & Consumer Scoring ETL
        // ---------------------------------------------------------------------
        stage('Stage 7: Real-Time Kafka Streaming & Scoring ETL') {
            when {
                expression { params.PIPELINE_MODE != 'CI_UNIT_ONLY' }
            }
            steps {
                echo '=================================================================='
                echo '>>> STAGE 7: REAL-TIME KAFKA STREAMING & SCORING ETL <<<'
                echo '=================================================================='
                script {
                    def nOnboard = (params.PIPELINE_MODE == 'FAST_DEMO') ? '50' : '100'
                    def nBio     = (params.PIPELINE_MODE == 'FAST_DEMO') ? '25' : '50'
                    if (isUnix()) {
                        sh """
                            python kafka_producer.py --n-events ${nOnboard} --delay 0.01
                            python kafka_consumer_etl.py --max-messages ${nOnboard}
                            python kafka_biometric_producer.py --n-events ${nBio} --delay 0.01
                            python kafka_biometric_consumer_etl.py --max-messages ${nBio}
                        """
                    } else {
                        bat """
                            python kafka_producer.py --n-events ${nOnboard} --delay 0.01
                            python kafka_consumer_etl.py --max-messages ${nOnboard}
                            python kafka_biometric_producer.py --n-events ${nBio} --delay 0.01
                            python kafka_biometric_consumer_etl.py --max-messages ${nBio}
                        """
                    }
                }
            }
        }

        // ---------------------------------------------------------------------
        // Stage 8: Continuous Drift Detection, Retraining & Progressive Canary
        // ---------------------------------------------------------------------
        stage('Stage 8: Continuous Drift Detection & Canary Rollout') {
            when {
                expression { params.PIPELINE_MODE != 'CI_UNIT_ONLY' }
            }
            steps {
                echo '=================================================================='
                echo '>>> STAGE 8: DRIFT DETECTION, RETRAINING & CANARY ROLLOUT <<<'
                echo '=================================================================='
                script {
                    if (isUnix()) {
                        sh '''
                            python drift_detection.py
                            python drift_metrics_exporter.py --once
                        '''
                        if (params.SIMULATE_DRIFT_AND_RETRAIN) {
                            sh 'python retraining_pipeline.py --simulate-drift'
                        }
                        if (params.CANARY_ROLLOUT) {
                            sh 'python canary_rollout_simulator.py'
                        }
                    } else {
                        bat '''
                            python drift_detection.py
                            python drift_metrics_exporter.py --once
                        '''
                        if (params.SIMULATE_DRIFT_AND_RETRAIN) {
                            bat 'python retraining_pipeline.py --simulate-drift'
                        }
                        if (params.CANARY_ROLLOUT) {
                            bat 'python canary_rollout_simulator.py'
                        }
                    }
                }
            }
        }
    }

    post {
        always {
            echo '=================================================================='
            echo '>>> POST-EXECUTION: PUBLISHING TEST REPORTS & ARTIFACTS <<<'
            echo '=================================================================='
            junit testResults: 'test-reports/*.xml', allowEmptyResults: true
        }
        success {
            echo '[+] Pipeline completed successfully! Archiving models, plots & reports.'
            archiveArtifacts(
                artifacts: '*.png, *.csv, *.pkl, dataset_model_change_registry.md, EVALUATION_REPORT_AND_GAP_CLOSURE.md',
                allowEmptyArchive: true,
                fingerprint: true
            )
        }
        failure {
            echo '[-] Pipeline failed! Check the console output above for the failing stage.'
        }
    }
}
