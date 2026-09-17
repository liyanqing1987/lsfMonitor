import copy
import pickle
import threading
import numpy as np
import pandas as pd
from tqdm import tqdm
from gensim.models import Word2Vec, KeyedVectors
from sklearn.cluster import KMeans


# ---------------------------------------------------------------------------
# 进程级模型缓存
#
# 在线预测服务(predict_web)对每个 job 都会调用 generate_*_feature，而这些方法
# 原本每次都从磁盘重新加载 word2vec / glove / kmeans 模型(glove 为几十 MB 的
# 文本文件，逐行解析很慢)。模型在服务进程生命周期内是不变的，因此按文件路径
# 缓存加载后的对象，避免重复磁盘 IO 与解析。
#
# 使用 threading.Lock 保护，gunicorn gevent worker 下并发请求安全。
# ---------------------------------------------------------------------------
_model_cache = {}
_model_cache_lock = threading.Lock()


def _cached_load(key, loader):
    """按 key 缓存 loader() 的返回值；命中缓存则直接返回，不重复加载。"""
    cached = _model_cache.get(key)

    if cached is not None:
        return cached

    with _model_cache_lock:
        # double-check：拿到锁后可能别的线程已经加载好了
        cached = _model_cache.get(key)

        if cached is not None:
            return cached

        obj = loader()
        _model_cache[key] = obj
        return obj


def _embed_sentence(words, keyed_vectors, emb_size):
    """对一个分词后的句子取词向量均值；无有效词时返回全 0 向量。"""
    vecs = [keyed_vectors[w] for w in words if w in keyed_vectors]

    if vecs:
        return np.mean(vecs, axis=0)

    return np.zeros(emb_size)


class Word2VecModel:
    def __init__(self, sentence_col, emb_size, model_path):
        (self.sentence_col, self.emb_size, self.model_path) = (sentence_col, emb_size, model_path)
        self.feature_name = 'word2vec'
        self.feature_columns_list = ['{}_{}_{}'.format(self.feature_name, self.sentence_col, i) for i in range(self.emb_size)]

    def _load_model(self):
        # Word2Vec.load 返回完整模型；在线推理只需要 wv (KeyedVectors)，
        # 缓存 wv 即可，占用更小、加载更快。
        def _load():
            return Word2Vec.load(self.model_path).wv

        return _cached_load('w2v:%s' % self.model_path, _load)

    def training_model(self, df, min_count=5, window=5):
        sentences = copy.deepcopy(df[self.sentence_col].values)

        for i in range(len(sentences)):
            sentences[i] = [str(x) for x in sentences[i]]

        w2v_model = Word2Vec(sentences, min_count=min_count, vector_size=self.emb_size, window=window)
        w2v_model.save(self.model_path)

        for i in tqdm(range(len(sentences))):
            sentences[i] = [w2v_model.wv[x] for x in sentences[i] if x in w2v_model.wv]

        emb_matrix = []

        for seq in tqdm(sentences):
            if len(seq) > 0:
                emb_matrix.append(np.mean(seq, axis=0))
            else:
                emb_matrix.append([0] * self.emb_size)

        emb_matrix = np.array(emb_matrix)
        emb_series_list = [pd.Series(emb_matrix[:, i]) for i in range(self.emb_size)]
        emb_df = pd.concat(emb_series_list, axis=1, keys=self.feature_columns_list)

        return emb_df

    def generate_word2vec_feature(self, df):
        sentences = copy.deepcopy(df[self.sentence_col].values)
        wv = self._load_model()

        # 在线服务每次只预测 1 条，走向量化快速路径，避免 tqdm 与逐行 Python 循环；
        # 批量场景(训练/调试)保留原 tqdm 逻辑。
        if len(sentences) == 1:
            words = [str(x) for x in sentences[0]]
            emb_matrix = np.array([_embed_sentence(words, wv, self.emb_size)])
        else:
            sentences = [[str(x) for x in s] for s in sentences]
            emb_matrix = []

            for seq in tqdm(sentences):
                vecs = [wv[x] for x in seq if x in wv]

                if len(vecs) > 0:
                    emb_matrix.append(np.mean(vecs, axis=0))
                else:
                    emb_matrix.append([0] * self.emb_size)

            emb_matrix = np.array(emb_matrix)

        emb_df = pd.DataFrame(emb_matrix, columns=self.feature_columns_list)

        return emb_df

    def kmeans_cluster(self, emb_df, save_path, n_cluster=8):
        word2vec_cluster_model = KMeans(n_clusters=n_cluster)
        emb_df = emb_df.astype('float64')
        word2vec_cluster_model.fit(emb_df)
        pickle.dump(word2vec_cluster_model, open(save_path, "wb"))

        label_df = pd.DataFrame()
        label_df[r'%s_%s_cluster_label' % (self.feature_name, self.sentence_col)] = word2vec_cluster_model.labels_

        return label_df

    def gen_cluster_label(self, emb_df, cluster_model_path):
        def _load():
            return pickle.load(open(cluster_model_path, "rb"))

        cluster_model = _cached_load('kmeans:%s' % cluster_model_path, _load)
        emb_df = emb_df.astype('float64')
        label = cluster_model.predict(emb_df)
        label_df = pd.DataFrame()
        label_df[r'%s_%s_cluster_label' % (self.feature_name, self.sentence_col)] = label

        return label_df


class GloVeModel:
    def __init__(self, sentence_col, emb_size, corpus_path, model_path):
        self.sentence_col = sentence_col
        self.emb_size = emb_size
        # 注意：这里的 model_path 传入的应该是那个 .txt 文件的路径
        self.model_path = f'{model_path}.txt'
        self.feature_name = 'glove'
        self.feature_columns_list = ['{}_{}_{}'.format(self.feature_name, self.sentence_col, i) for i in range(self.emb_size)]

    def _load_model(self):
        # glove 以 word2vec 文本格式保存，KeyedVectors.load_word2vec_format 逐行
        # 解析几十 MB 文本非常慢，是在线预测的主要瓶颈，必须缓存。
        def _load():
            return KeyedVectors.load_word2vec_format(self.model_path, binary=False)

        return _cached_load('glove:%s' % self.model_path, _load)

    def training_model(self, df):
        sentences = [[str(x) for x in s] for s in df[self.sentence_col].values]
        from gensim.models import Word2Vec
        model = Word2Vec(
            sentences,
            vector_size=self.emb_size,
            window=10,
            min_count=1,
            workers=4,
            seed=1024,
            epochs=10
        )

        # model.save(self.model_path)
        model.wv.save_word2vec_format(self.model_path, binary=False)
        vocab = model.wv.key_to_index

        emb_matrix = []
        for seq in tqdm(sentences):
            vecs = [model.wv[word] for word in seq if word in vocab]
            if len(vecs) > 0:
                emb_matrix.append(np.mean(vecs, axis=0))
            else:
                emb_matrix.append(np.zeros(self.emb_size))

        emb_matrix = np.array(emb_matrix)
        emb_df = pd.DataFrame(emb_matrix, columns=self.feature_columns_list)

        return emb_df

    def generate_glove_feature(self, df):
        sentences = [[str(x) for x in s] for s in df[self.sentence_col].values]
        wv = self._load_model()

        if len(sentences) == 1:
            emb_matrix = np.array([_embed_sentence(sentences[0], wv, self.emb_size)])
        else:
            emb_matrix = []

            for seq in tqdm(sentences):
                vecs = [wv[x] for x in seq if x in wv]

                if len(vecs) > 0:
                    emb_matrix.append(np.mean(vecs, axis=0))
                else:
                    emb_matrix.append(np.zeros(self.emb_size))

            emb_matrix = np.array(emb_matrix)

        emb_df = pd.DataFrame(emb_matrix, columns=self.feature_columns_list)
        return emb_df

    def kmeans_cluster(self, emb_df, save_path, n_cluster=8):
        glove_cluster_model = KMeans(n_clusters=n_cluster)
        emb_df = emb_df.astype('float64')
        glove_cluster_model.fit(emb_df)
        pickle.dump(glove_cluster_model, open(save_path, "wb"))

        label_df = pd.DataFrame()
        label_df[r'%s_%s_cluster_label' % (self.feature_name, self.sentence_col)] = glove_cluster_model.labels_

        return label_df

    def gen_cluster_label(self, emb_df, cluster_model_path):
        def _load():
            return pickle.load(open(cluster_model_path, "rb"))

        cluster_model = _cached_load('kmeans:%s' % cluster_model_path, _load)
        emb_df = emb_df.astype('float64')
        label = cluster_model.predict(emb_df)
        label_df = pd.DataFrame()
        label_df[r'%s_%s_cluster_label' % (self.feature_name, self.sentence_col)] = label

        return label_df
