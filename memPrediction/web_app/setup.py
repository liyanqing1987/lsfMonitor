import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

import requests
from elasticsearch_dsl import Search, A

sys.path.append(str(os.environ['MEM_PREDICTION_INSTALL_PATH']))
from common.common import get_logger
from web_app.backend import memory_web, dataCollector
from common.common_es import ESDB
from config import config


logger = get_logger()
version = '8.17.0'
web_app_path = os.path.join(str(os.environ['MEM_PREDICTION_INSTALL_PATH']), 'web_app')


def read_args():
    """
    Read in arguments.
    """
    parser = argparse.ArgumentParser()

    web_app_arg = parser.add_argument_group('Memory Prediction Web App')
    web_app_arg.add_argument('--db', action='store_true', default=False, help='Elastic Search Setup')
    web_app_arg.add_argument('--backend', action='store_true', default=False, help='Web Backend Service Setup')
    web_app_arg.add_argument('--frontend', action='store_true', default=False, help='Web Frontend Service Setup')
    web_app_arg.add_argument('--es', default='', help='Frontend Service Setup')
    web_app_arg.add_argument('--node', default='', help='Frontend Service node_modules')
    web_app_arg.add_argument('--collect', action='store_true', help='Data Collection')

    args = parser.parse_args()

    return args


def find_free_port(start_port=9200):
    port = start_port

    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("0.0.0.0", port))
            sock.listen(1)
            sock.close()
            return port
        except socket.error:
            port += 1


def setup_db(es: str) -> int:
    port = str(find_free_port())
    timeout_interval = 120

    if es:
        if not os.path.exists(es):
            logger.error(f'Elastic Search tar {es} does not exists.')
            sys.exit(1)
        elif os.path.basename(es) != f'elasticsearch-{version}-linux-x86_64.tar.gz':
            logger.error(f'Unsupported Elastic Search {es}.')
            sys.exit(1)

        setup_script = f"""
#!/bin/bash
cp -rf {es} {web_app_path}/
tar -xzf {web_app_path}/elasticsearch-{version}-linux-x86_64.tar.gz
{web_app_path}/elasticsearch-{version}/bin/elasticsearch -E http.port={port}
"""

    else:
        setup_script = f"""
#!/bin/bash
DOWNLOAD_URL="https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-${version}-linux-x86_64.tar.gz"
curl -L -O $DOWNLOAD_URL
tar -xzf {web_app_path}/elasticsearch-{version}-linux-x86_64.tar.gz
{web_app_path}/elasticsearch-{version}/bin/elasticsearch -E http.port={port}
"""

    try:
        logger.info('Starting the Elasticsearch server...')
        subprocess.Popen(setup_script, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        timeout = time.time() + timeout_interval

        while True:
            if time.time() > timeout:
                logger.error('Failed to start Elasticsearch within 120 seconds.')
                sys.exit(1)

            try:
                response = requests.get(f'https://127.0.0.1:{port}', verify=False)
                if response.status_code == 401:
                    logger.info('Elasticsearch has started successfully.')
                    break
                else:
                    logger.info('Elasticsearch is not ready yet. Status code:', response.status_code)
            except requests.exceptions.RequestException as e:
                logger.info(f'Elasticsearch is not ready yet. Trying again in 10 seconds... Error {str(e)}')

            time.sleep(10)

    except subprocess.SubprocessError as e:
        logger.error(f'Failed to start the Elasticsearch process: {e}')
    except Exception as e:
        logger.error(f'An unexpected error occurred: {e}')

    return int(port)


def setup_db_yaml(port: int = 9200):
    user = 'elastic'

    logger.info('Reset Password for Elastic Search')
    setup_script = f"""
#!/bin/bash
cd {web_app_path}
{web_app_path}/elasticsearch-{version}/bin/elasticsearch-reset-password -u {user}
bash {web_app_path}/conf/create_es_template.sh
"""
    process = subprocess.Popen(setup_script, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    output, errors = process.communicate(input='y\n'.encode())
    logger.info('Reset Password')
    logger.error(f'{errors.decode()}')
    logger.info(f'{output.decode()}')

    password = ((output.decode()).split('\n')[-2]).split()[-1]
    url = f'https://127.0.0.1:{port}'
    ca_cert = os.path.join(web_app_path, f'elasticsearch-{version}/config/certs/http_ca.crt')
    config = {'USER': user, 'PASS': password, 'CERT': ca_cert, 'ESURL': url}
    config_files = [
        os.path.join(str(os.environ['MEM_PREDICTION_INSTALL_PATH']), 'config/web_app.yaml'),
        os.path.join(str(os.environ['MEM_PREDICTION_INSTALL_PATH']), 'web_app/conf/create_es_template.sh')
    ]

    logger.info('Rewrite configuration.')

    for config_file in config_files:
        with open(config_file, 'r') as file:
            content = file.read()

        for key, value in config.items():
            placeholder = f"{{{key}}}"
            content = content.replace(placeholder, value)

        with open(config_file, 'w') as file:
            file.write(content)

    logger.info('Rewrite configuration done.')


def setup_flask():
    logger.info('Starting the backend Flask server ...')
    port = find_free_port(start_port=12345)
    flask_thread = threading.Thread(target=memory_web.run_flask, args=('0.0.0.0', port))
    flask_thread.start()
    logger.info('Starting the backend Flask server done.')

    logger.info('Rewrite configuration ')
    hostname = socket.gethostname()
    ip_address = socket.gethostbyname(hostname)
    config = {'apiUrl': f'http://{ip_address}:{str(port)}'}
    config_files = [
        os.path.join(str(os.environ['MEM_PREDICTION_INSTALL_PATH']), 'web_app/frontend/src/conf.tsx')
    ]

    logger.info('Rewrite configuration.')

    for config_file in config_files:
        with open(config_file, 'r') as file:
            content = file.read()

        for key, value in config.items():
            placeholder = f"{{{key}}}"
            content = content.replace(placeholder, value)

        with open(config_file, 'w') as file:
            file.write(content)

    logger.info('Rewrite configuration done.')


def setup_frontend(node: str = ''):
    logger.info('Starting the frontend Flask server ...')
    if node and os.path.exists(node):
        shutil.copytree(node, os.path.join(web_app_path, os.path.basename(node)))
        setup_script = f"""
#!/bin/bash
cd {web_app_path}/frontend/
npm run build
npm run preview
"""
    else:
        setup_script = f"""
#!/bin/bash
cd {web_app_path}/frontend/
npm install --legacy-peer-deps
npm run build
npm run preview
"""
    os.system(setup_script)


def data_collect():
    logger.info('Data Collector...')
    es = ESDB()
    es.create_es_client()
    job_indices = [dataCollector.DataCollector.extract_file_date(index) for index in es.es_client.indices.get('job*')]
    summary_indices = [dataCollector.DataCollector.extract_file_date(index) for index in es.es_client.indices.get('summary*')]
    command = "#!/bin/bash\n"

    for root, dirs, files in os.walk(config.db_path):
        for file in files:
            file_date = dataCollector.DataCollector.extract_file_date(file)

            if file_date not in job_indices:
                command += f'bash {web_app_path}/backend/dataCollector -f {os.path.join(config.db_path, file)} --job\n'

            if file_date not in summary_indices:
                command += f'bash {web_app_path}/backend/dataCollector -f {os.path.join(config.db_path, file)} --summary\n'

    logger.info(f'Command: {command}')
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    output, errors = process.communicate(input='y\n'.encode())
    logger.info('Data Collector Done')
    logger.error(f'{errors.decode()}')
    logger.info(f'{output.decode()}')


def main():
    args = read_args()

    if args.db:
        es_port = setup_db(es=args.es)
        setup_db_yaml(port=es_port)

    if args.backend:
        setup_flask()

    if args.frontend:
        setup_frontend(node=args.node)

    if args.collect:
        data_collect()


if __name__ == '__main__':
    main()
