import datetime
import json
import os
import sys
import logging

import pandas as pd
from elasticsearch_dsl import Search, A

sys.path.append(str(os.environ['MEM_PREDICTION_INSTALL_PATH']))

from common.common import get_logger
from common.common_es import ESDB

logger = get_logger(name='memory_web', level=logging.DEBUG)

from flask import Flask, request, jsonify

app = Flask(__name__)


@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response


class DataProcessor:
    def __init__(self):
        self.es_db = ESDB()
        self.es_db.create_es_client()
        self.cache = {}

    def get_job_data(self, start_date: str, end_date: str, user: str) -> pd.DataFrame:
        key = f'start_{start_date}_end_{end_date}_user_{user}'
        if key in self.cache:
            return self.cache.get(key)

        s = Search(using=self.es_db.es_client, index='job_*')
        s = s.query("range", date={"gte": start_date, "lte": end_date, "format": "yyyy-MM-dd"})
        s = s.query("term", user={"value": user})

        s = s.extra(size=1000)
        s = s.sort('-date')

        response = s.execute()

        data = []
        for hit in response.hits:
            hit_dict = hit.to_dict()
            data.append(hit_dict)

        df = pd.DataFrame(data)
        if not df.empty:
            df.fillna(0, inplace=True)

        self.cache[key] = df
        return df

    def get_job(self, start_date: str, end_date: str, job_id: str, user: str) -> pd.DataFrame:
        try:
            job_id = int(job_id)
            s = Search(using=self.es_db.es_client, index='job_*')
            s = s.filter("range", date={"gte": start_date, "lte": end_date, "format": "yyyy-MM-dd"})
            s = s.filter("term", job_id={"value": job_id})
            res = s.execute()
            data = [hit.to_dict() for hit in res]
            df = pd.DataFrame(data)
            serialized_df = df.applymap(self.custom_serializer)
            serialized_df.dropna(inplace=True)
            return serialized_df
        except Exception:
            return self.get_job_data(start_date=start_date, end_date=end_date, user=user)

    @staticmethod
    def custom_serializer(obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        elif isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        elif isinstance(obj, (dict, list, tuple)):
            return json.dumps(obj)
        else:
            return obj

    def get_summary_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        key = f'start_{start_date}_end_{end_date}_summary'

        if key in self.cache:
            return self.cache.get(key)

        s = Search(using=self.es_db.es_client, index='summary_*')
        s = s.filter("range", date={"gte": start_date, "lte": end_date, "format": "yyyy-MM-dd"})
        a = A('terms', field='user', size=1000)
        a.metric('insufficient_mem_quantity', 'sum', field='insufficient_mem_quantity')
        a.metric('excess_mem_quantity', 'sum', field='excess_mem_quantity')
        a.metric('job_num', 'sum', field='job_num')
        a.metric('excess_cpu_quantity', 'sum', field='excess_cpu_quantity')
        a.metric('95_quantile_mem', 'avg', field='95_quantile_mem')
        a.metric('max_mem_sum', 'sum', field='max_mem_sum')
        a.metric('rusage_mem_sum', 'sum', field='rusage_mem_sum')
        a.metric('job_duration_sum', 'sum', field='job_duration_sum')
        s.aggs.bucket('user_aggregation', a)
        s = s.extra(size=0)
        res = s.execute()

        data = []

        for bucket in res.aggregations.user_aggregation.buckets:
            data.append({
                "user": bucket.key,
                "insufficient_mem_quantity": bucket.insufficient_mem_quantity.value,
                "excess_mem_quantity": bucket.excess_mem_quantity.value,
                "job_num": bucket.job_num.value,
                "excess_cpu_quantity": bucket.excess_cpu_quantity.value,
                "max_mem_quantile_95": bucket['95_quantile_mem'].value,
                "max_mem_average": bucket.max_mem_sum.value / bucket.job_num.value,
                "rusage_mem_average": bucket.rusage_mem_sum.value / bucket.job_num.value,
                "job_duration_average": bucket.job_duration_sum.value / bucket.job_num.value
            })

        df = pd.DataFrame(data, columns=['user', 'insufficient_mem_quantity', 'excess_mem_quantity', 'job_num', 'excess_cpu_quantity',
                                         'max_mem_quantile_95', 'max_mem_average', 'rusage_mem_average', 'job_duration_average'])
        df['job_duration_average'] = df['job_duration_average'].round(1)
        df['max_mem_quantile_95'] = df['max_mem_quantile_95'].round(3)
        df['max_mem_average'] = df['max_mem_average'].round(3)
        df['rusage_mem_average'] = df['rusage_mem_average'].round(3)
        df = df.sort_values(by='excess_mem_quantity', ascending=False)
        self.cache[key] = df

        return df


data_processor = DataProcessor()


@app.route('/', methods=['OPTIONS'])
def handle_root_options():
    return jsonify({})


@app.route('/job', methods=['POST', 'OPTIONS'])
def get_user_job():
    if request.method == 'OPTIONS':
        return jsonify({})

    data = request.json
    start_date = data['start_date']
    end_date = data['end_date']
    user = data['user']

    logger.debug(f'Filters: Start Date {start_date} End Date {end_date} User {user}')
    job_data = data_processor.get_job_data(start_date=start_date, end_date=end_date, user=user)
    logger.debug('Job Records: {}'.format(str(len(job_data.to_dict(orient='records')))))

    return jsonify(job_data.to_dict(orient='records'))


@app.route('/job_id', methods=['POST', 'OPTIONS'])
def get_job_id():
    if request.method == 'OPTIONS':
        return jsonify({})

    data = request.json
    start_date = data['start_date']
    end_date = data['end_date']
    job_id = data['job_id']
    user = data['user']

    logger.debug(f'Filters: Start Date {start_date} End Date {end_date} Job_ID {job_id} User {user}')
    job_data = data_processor.get_job(start_date=start_date, end_date=end_date, job_id=job_id, user=user)
    logger.debug('Job Records: {}'.format(str(len(job_data.to_dict(orient='records')))))

    return jsonify(job_data.to_dict(orient='records'))


@app.route('/summary', methods=['POST', 'OPTIONS'])
def get_summary():
    if request.method == 'OPTIONS':
        return jsonify({})

    data = request.json
    start_date = data['start_date']
    end_date = data['end_date']

    logger.debug(f'Filters: Start Date {start_date} End Date {end_date}')
    job_data = data_processor.get_summary_data(start_date=start_date, end_date=end_date)
    logger.debug('Summary Records: {}'.format(str(len(job_data.to_dict(orient='records')))))

    return jsonify(job_data.to_dict(orient='records'))


def run_flask(host: str, port: int):
    app.run(host=host, port=port, threaded=True)


if __name__ == '__main__':
    app.run(debug=True, port=11112, host='0.0.0.0')


