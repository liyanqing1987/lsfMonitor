# -*- coding: utf-8 -*-
import os
import yaml
from es_pandas import es_pandas
from elasticsearch import Elasticsearch, exceptions
import pandas as pd

from common import common

logger = common.get_logger()


def read_conf(file_name) -> dict:
    file_path = os.path.join(os.environ['MEM_PREDICTION_INSTALL_PATH'], f'config/{file_name}')

    if not os.path.exists(file_path):
        logger.warning(f'Missing configuration file : {file_path}')
        raise RuntimeError

    with open(file_path, 'r') as fd:
        data = yaml.load(fd, Loader=yaml.FullLoader)

    return data if data is not None else {}


class ESDB:
    def __init__(self):
        self.config = read_conf('web_app.yaml').get('ESDB')
        self.ep_client = None
        self.es_client = None

    def create_ep_client(self):
        """
        Create Elastic Pandas client
        """
        params = {}
        es_url = self.config.get('ESURL')
        es_timeout = self.config.get('TIMEOUT', 1)
        es_max_retries = self.config.get('MAX_RETRIES', 5)
        es_auth = self.config.get('ES_AUTH', False)
        es_user = self.config.get('ES_USER')
        es_pass = self.config.get('ES_PASS')
        es_cert = self.config.get('ES_CERT')

        if es_user and es_pass and es_auth:
            params['http_auth'] = (es_user, es_pass)
        if es_cert:
            params['use_ssl'] = True
            params['ca_certs'] = es_cert

        params['timeout'] = es_timeout
        params['max_retries'] = es_max_retries
        params['retry_on_timeout'] = True

        try:
            self.ep_client = es_pandas(es_url, **params)
        except Exception as e:
            logger.error("Create Elastic Pandas client failed! Error: {}".format(str(e)))

    def create_es_client(self):
        """
        Create Elastic client
        """
        params = {}
        es_url = self.config.get('ESURL')
        es_timeout = self.config.get('TIMEOUT', 1)
        es_max_retries = self.config.get('MAX_RETRIES', 5)
        es_auth = self.config.get('ES_AUTH', False)
        es_user = self.config.get('ES_USER')
        es_pass = self.config.get('ES_PASS')
        es_cert = self.config.get('ES_CERT')
        if es_user and es_pass and es_auth:
            params['http_auth'] = (es_user, es_pass)
        if es_cert:
            params['use_ssl'] = True
            params['ca_certs'] = es_cert
        params['timeout'] = es_timeout
        params['max_retries'] = es_max_retries
        params['retry_on_timeout'] = True

        try:
            self.es_client = Elasticsearch(es_url, **params)
        except Exception as e:
            logger.error("Create Elastic Pandas client failed! Error: {}".format(str(e)))

    def get_data(self, dbname, dtype=None, **kwargs):
        """
        :param dbname: ES index表名称，必要
        :param query:  ES DSL查询，可选。
        :return: 成功/失
        """
        data = None
        ret = True

        try:
            data = self.ep_client.to_pandas(dbname, dtype=dtype, infer_dtype=True, show_progress=False, **kwargs)
        except exceptions.NotFoundError:
            logger.debug("Not found index: {}".format(dbname))
            pass
        except Exception as e:
            ret = False
            logger.error("Get data failed! dbname: {}, Error: {}".format(dbname, str(e)))

        return ret, data

    def save_data(self, dbname, df, **kwargs):
        """
        保存数据 到 ES 数据库
        :param epcon  :  ES-Pandas连接，必要
        :param dbname :  ES index表名称，必要
        :param df     :  要保存的数据，Pandas DataFrame类型，必要
        :return: 成功或失败
        """
        ret = True
        try:
            self.ep_client.to_es(df, dbname, use_index=True, _op_type='create', show_progress=False, refresh='true', **kwargs)
        except ConnectionError:
            ret = False
            logger.error("Save data failed! dbname: {}, connection error!".format(dbname))
        except Exception as e:
            ret = False
            logger.error("Save data failed! dbname: {}, Error: {}".format(dbname, str(e)))

        return ret

    def update_data(self, dbname, df, **kwargs):
        """
        更新 ES 数据库中的数据
        :param dbname :  ES index表名称，必要
        :param df     :  要更新的数据，Pandas DataFrame类型，必要
        :return: 成功或失败
        """
        ret = True
        try:
            self.ep_client.to_es(df, dbname, use_index=True, _op_type='update', show_progress=False, refresh='true', **kwargs)
        except Exception as e:
            ret = False
            logger.error("Update data failed! dbname: {}, Error: {}".format(dbname, str(e)))

        return ret

    def del_data(self, dbname, df, **kwargs):
        """
        从 ES 数据库删除数据
        :param dbname :  ES index表名称，必要
        :param df     :  要删除的数据，Pandas DataFrame类型，必要
        :return: 成功或失败
        """
        ret = True
        try:
            self.ep_client.to_es(df, dbname, use_index=True, _op_type='delete', show_progress=False, refresh='true', **kwargs)
        except Exception as e:
            ret = False
            logger.error("Delete data failed! Params: dbname[{}] data[{}], Error[{}]".format(dbname, df, str(e)))

        return ret

    def get_update(self, df1, df2):
        """
        param: df1 - Pandas DataFrame 原数据
        param: df2 - Pandas DataFrame 新数据
        return: 需要更新数据行，类型为Pandas DataFrame
        """
        # 初始化返回数据为空
        ud = pd.DataFrame()

        # 数据基本验证：不能为空和 None
        if (df1 is None or len(df1) <= 0) or (df2 is None or len(df2) <= 0):
            return ud

        index = []
        for i in df1.index.to_list():
            row1 = df1.loc[i]
            row2 = df2.loc[i]
            d1 = row1.to_json()
            d2 = row2.to_json()
            if d1 != d2:
                index.append(i)
        return df2.loc[index]

    def compare_data(self, df_pre, df_new):
        """
        比较数据库中的数据与新采集的数据，确定要在数据库中删除的数据、需要插入的数据和需要更新的数据
        :param df_pre  :  从数据库中获得的原数据，必要，类型为Pandas DataFrame
        :param df_new  :  新采集数据，必要，类型为Pandas DataFrame
        :return: 要保存到数据库的数据、要删除的数据和要更新的数据，类型均为Pandas DataFrame
        """

        df_save = None
        df_del = None
        df_update = None

        # 如果新采集数据为空，则删除数据库的所有数据
        if df_new is None or df_new.size <= 0:
            df_del = df_pre
            return df_save, df_del, df_update

        # 如果数据库中没有数据，则保存所有新采集数据
        if df_pre is None or df_pre.size <= 0:
            df_save = df_new
            return df_save, df_del, df_update

        # 找出需要删除和添加记录的索引
        pre_index = set(df_pre.index.to_list())
        new_index = set(df_new.index.to_list())
        cr = pre_index & new_index
        dr = pre_index - cr
        ar = new_index - cr

        # 根据索引得到数据
        df_save = df_new.loc[list(ar)] if len(ar) else None
        df_del = df_pre.loc[list(dr)] if len(dr) else None

        d1 = df_pre.loc[list(cr)] if len(cr) else None
        d2 = df_new.loc[list(cr)] if len(cr) else None

        if d1 is not None and len(d1):
            df_update = self.get_update(d1, d2)

        del pre_index
        del new_index
        del cr
        del dr
        del ar
        del d1
        del d2

        return df_save, df_del, df_update