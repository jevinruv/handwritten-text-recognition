import numpy as np
import tensorflow as tf

from Constants import Constants
from DataHandler import DataHandler


class Model:

    def __init__(self, char_list, restore=False):

        self.decoder_selected = Constants.decoder_selected
        self.path_model = Constants.path_model
        self.batch_size = Constants.batch_size
        self.char_list = char_list
        self.learning_rate = Constants.learning_rate
        self.text_length = Constants.text_length
        self.img_size = Constants.img_size
        self.file_word_char_list = Constants.file_word_char_list
        self.file_word_beam_search = Constants.file_word_beam_search
        self.file_collection_words = Constants.file_collection_words

        self.is_restore = restore
        self.model_id = 0
        self.is_train = tf.placeholder(tf.bool, name='is_train')
        self.input_images = tf.placeholder(tf.float32, shape=(None, self.img_size[0], self.img_size[1]))

        self.initialize()

    def initialize(self):

        self.build_CNN()
        self.build_RNN()
        self.build_CTC()

        self.trained_batches = 0
        self.learning_rate = tf.placeholder(tf.float32, shape=[])
        self.update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)

        with tf.control_dependencies(self.update_ops):
            self.optimizer = tf.train.RMSPropOptimizer(self.learning_rate).minimize(self.loss_batch)

        self.sess = tf.Session()

        self.saver = tf.train.Saver(max_to_keep=1)
        model = tf.train.latest_checkpoint(self.path_model)

        if self.is_restore and not model:
            raise Exception('Model Not found')

        # load saved model if available
        if model:
            print('Restoring Model ' + model)
            self.saver.restore(self.sess, model)
        else:
            print('New Model')
            self.sess.run(tf.global_variables_initializer())

    def save(self):

        self.model_id += 1
        self.saver.save(self.sess, self.path_model + 'model', global_step=self.model_id)

    def build_CNN(self):

        cnn_input_4d = tf.expand_dims(input=self.input_images, axis=3)  # adds dimensions of size 1 to the 3rd index

        pool = cnn_input_4d

        pool = self.create_CNN_layer(pool, filter_size=5, in_features=1, out_features=32, max_pool=(2, 2))
        pool = self.create_CNN_layer(pool, filter_size=5, in_features=32, out_features=64, max_pool=(2, 2))
        pool = self.create_CNN_layer(pool, filter_size=3, in_features=64, out_features=128, max_pool=(1, 2))
        pool = self.create_CNN_layer(pool, filter_size=3, in_features=128, out_features=128, max_pool=(1, 2))
        pool = self.create_CNN_layer(pool, filter_size=3, in_features=128, out_features=256, max_pool=(1, 2))

        self.cnn_output_4d = pool

    def create_CNN_layer(self, pool, filter_size, in_features, out_features, max_pool):

        # initialize weights
        filter = tf.Variable(tf.truncated_normal([filter_size, filter_size, in_features, out_features], stddev=0.1))

        conv = tf.nn.conv2d(input=pool, filter=filter, padding='SAME', strides=(1, 1, 1, 1))
        conv_norm = tf.layers.batch_normalization(conv, training=self.is_train)
        relu = tf.nn.relu(conv_norm)
        pool = tf.nn.max_pool(relu,
                              ksize=(1, max_pool[0], max_pool[1], 1),
                              strides=(1, max_pool[0], max_pool[1], 1),
                              padding='VALID')

        # layer 1
        # filter = tf.Variable(tf.truncated_normal([5, 5, 1, 32], stddev=0.1))
        # conv = tf.nn.conv2d(input=pool, filter=filter, padding='SAME', strides=(1, 1, 1, 1)) # strides=[1, 1, 1, 1], the filter window will move 1 batch, 1 height pixel, 1 width pixel and 1 color pixel
        # relu = tf.nn.relu(conv)
        # pool = tf.nn.max_pool(relu, ksize=(1, 2, 2, 1), strides=(1, 2, 2, 1), padding='VALID')

        return pool

    def build_RNN(self):

        rnn_input_3d = tf.squeeze(input=self.cnn_output_4d, axis=[2])  # reduces the dimension by deleting 2nd index

        # define no. of cells & layers to build
        n_cell = 256
        n_layers = 2
        cells = []

        for _ in range(n_layers):
            cells.append(tf.contrib.rnn.LSTMCell(num_units=n_cell, state_is_tuple=True))

        # combine the 2 simple LSTM cells sequentially
        cell_multi = tf.contrib.rnn.MultiRNNCell(cells, state_is_tuple=True)

        ((fw, bw), _) = tf.nn.bidirectional_dynamic_rnn(cell_fw=cell_multi,
                                                        cell_bw=cell_multi,
                                                        inputs=rnn_input_3d,
                                                        dtype=rnn_input_3d.dtype)

        rnn_combined = tf.concat([fw, bw], 2)  # combine the fw & bw
        rnn = tf.expand_dims(rnn_combined, 2)  # adds dimensions of size 1 to the 2nd index

        features_in = n_cell * 2  # no. of input
        features_out = len(self.char_list) + 1  # no. of output, characters + blank space

        kernel = tf.Variable(tf.truncated_normal([1, 1, features_in, features_out], stddev=0.1))
        rnn = tf.nn.atrous_conv2d(value=rnn, filters=kernel, rate=1, padding='SAME')

        self.rnn_output_3d = tf.squeeze(rnn, axis=[2])  # reduces the dimension by deleting 2nd index

    def build_CTC(self):

        # transform the rnn_output dimension
        self.ctc_input_3d = tf.transpose(self.rnn_output_3d, [1, 0, 2])

        # transform label to tensor
        self.labels = tf.SparseTensor(tf.placeholder(tf.int64, shape=[None, 2]),
                                      tf.placeholder(tf.int32, [None]),
                                      tf.placeholder(tf.int64, [2]))

        self.seq_length = tf.placeholder(tf.int32, [None])

        # calculate the loss & return the mean
        loss_batch_mean = tf.nn.ctc_loss(labels=self.labels,
                                         inputs=self.ctc_input_3d,
                                         sequence_length=self.seq_length,
                                         ctc_merge_repeated=True)

        self.loss_batch = tf.reduce_mean(loss_batch_mean)

        self.ctc_input_element = tf.placeholder(tf.float32, shape=[self.text_length, None, len(self.char_list) + 1])

        # calculate the loss per each element to find the label score
        self.loss_element = tf.nn.ctc_loss(labels=self.labels,
                                           inputs=self.ctc_input_element,
                                           sequence_length=self.seq_length,
                                           ctc_merge_repeated=True)

        if self.decoder_selected == Constants.decoder_best_path:
            print("Decoder Greedy")
            self.decoder = tf.nn.ctc_greedy_decoder(inputs=self.ctc_input_3d, sequence_length=self.seq_length)

        elif self.decoder_selected == Constants.decoder_word_beam:
            print("Decoder Word Beam")
            self.load_word_beam()

    def load_word_beam(self):

        word_beam_search_module = tf.load_op_library(self.file_word_beam_search)

        chars = str().join(self.char_list)
        word_chars = open(self.file_word_char_list).read().splitlines()[0]

        data_handler = DataHandler()
        data_handler.prepare_collection_words()
        collection_words = open(self.file_collection_words).read()

        # decode the recognized word against the provided address dictionary
        self.decoder = word_beam_search_module.word_beam_search(
            tf.nn.softmax(self.ctc_input_3d, dim=2),
            50,  # batch size
            'Words',  # sentence or word
            0.0,  # smoothing
            collection_words.encode('utf8'),
            chars.encode('utf8'),
            word_chars.encode('utf8'))

    def encode(self, texts):
        "transform labels to sparse tensor"

        indices = []
        values = []
        shape = [len(texts), 0]

        # iterate over the labels (texts)
        for (batch_element, text) in enumerate(texts):

            label_list = []

            for c in text:
                character = self.char_list.index(c)
                label_list.append(character)

            # check label list length and assign it to shape array
            if len(label_list) > shape[1]:
                shape[1] = len(label_list)

            # transform label to tensor
            for (i, label) in enumerate(label_list):
                indices.append([batch_element, i])
                values.append(label)

        return (indices, values, shape)

    def decode(self, ctc_output, batch_size):
        "transform sparse tensor to labels"

        encoded_label_list = []  # store batch elements labels

        for i in range(batch_size):
            encoded_label_list.append([])

        blank = len(self.char_list)  # last char is a blank

        # transform tensor to char indexes
        for j in range(batch_size):

            for label in ctc_output[j]:
                if label == blank:
                    break
                encoded_label_list[j].append(label)

        # convert char indexes to words
        word_list = []

        for label in encoded_label_list:

            word = []
            for c in label:
                char = self.char_list[c]
                word.append(char)

            word_list.append(str().join(word))

        return word_list

    def batch_train(self, batch):

        n_batch_elements = len(batch.imgs)
        sparse = self.encode(batch.labels)

        rate = 0

        if self.trained_batches < 10:
            rate = 0.01
        else:
            if self.trained_batches < 10000:
                rate = 0.001
            else:
                rate = 0.0001

        evaluation_list = [self.optimizer, self.loss_batch]

        data_train = {self.input_images: batch.imgs,
                      self.labels: sparse,
                      self.seq_length: [self.text_length] * n_batch_elements,
                      self.learning_rate: rate,
                      self.is_train: True}

        (_, loss) = self.sess.run(evaluation_list, data_train)
        self.trained_batches += 1

        return loss

    def batch_test(self, batch):

        n_batch_elements = len(batch.imgs)

        data_test = {self.input_images: batch.imgs,
                     self.seq_length: [self.text_length] * n_batch_elements,
                     self.is_train: False}

        result = self.sess.run([self.decoder, self.ctc_input_3d], data_test)

        char_score = result[0]
        recognized_texts = self.decode(char_score, n_batch_elements)

        return recognized_texts
