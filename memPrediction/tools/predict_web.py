# -*- coding: utf-8 -*-
# ruff: noqa
################################
# File Name   : predict_web.py
# Author      : zhangjingwen.silvia
# Created On  : 2023-12-14 14:28:30
# Description :
################################
import os
import sys
import yaml
import logging
import pandas as pd
from flask import Flask, request
from flask_restful import Api, Resource

sys.path.append(str(os.environ['MEM_PREDICTION_INSTALL_PATH']))

from common import common
from config import config
from bin import predict

logger = common.get_logger(name='root', level=logging.INFO)
config = os.path.join(config.predict_model, 'config/config')

if not os.path.exists(config):
    logger.error("Could not find config: %s, please check!" % config)
    sys.exit(1)

with open(config, 'r') as cf:
    config_dic = yaml.load(cf, Loader=yaml.FullLoader)

predict_model = predict.PredictModel(config_dic)

# 启动时预热 embedding 模型到进程缓存；配合 gunicorn preload_app，
# worker fork 后即命中缓存，首个请求也不会有模型加载冷启动延迟。
predict_model.warmup()
logger.info("predict model warmup done, ready to serve.")


class MemoryPredictServer(Resource):
    def post(self):
        data = request.form.to_dict()

        # esub 历史上把 started_time 误拼成 strated_time，这里兼容两种拼写，
        # 否则 started_time 列缺失会导致时间特征(day_of_weekday/hour_of_day/month)
        # 全部退化为默认值。
        if 'started_time' not in data and 'strated_time' in data:
            data['started_time'] = data['strated_time']

        data = pd.DataFrame(data, index=[0])

        try:
            predict_memory = predict_model.predict(False, data)
        except Exception:
            predict_memory = 1024

        # numpy ndarray / numpy scalar -> python 原生标量，否则 json 无法序列化
        if hasattr(predict_memory, 'tolist'):
            predict_memory = predict_memory.tolist()
            if isinstance(predict_memory, list):
                predict_memory = predict_memory[0] if predict_memory else None

        return predict_memory


app = Flask(__name__)
api = Api(app)
api.add_resource(MemoryPredictServer, "/memPrediction")
