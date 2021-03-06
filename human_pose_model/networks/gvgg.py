from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf
from tensorflow.python.framework import ops
import pdb
slim = tf.contrib.slim


LATENT_DIM = 4

def gvgg_vae_arg_scope(weight_decay=0.0005):
    """Defines the GVGG arg scope.

    Args:
      weight_decay: The l2 regularization coefficient.

    Returns:
      An arg_scope.
    """
    with slim.arg_scope([slim.conv2d, slim.fully_connected],
                         activation_fn=tf.nn.relu,
                         weights_regularizer=slim.l2_regularizer(weight_decay),
                         biases_initializer=tf.zeros_initializer()):
        with slim.arg_scope([slim.conv2d], padding='SAME') as arg_sc:
            return arg_sc


def gvgg_arg_scope(weight_decay=0.0005):
    """Defines the GVGG arg scope.

    Args:
      weight_decay: The l2 regularization coefficient.

    Returns:
      An arg_scope.
    """
    with slim.arg_scope([slim.conv2d],
                         activation_fn=tf.nn.relu,
                         weights_regularizer=slim.l2_regularizer(weight_decay),
                         padding='SAME',
                         biases_initializer=tf.zeros_initializer()) as arg_sc:
        return arg_sc


def _encoder(inputs, num_classes, dropout_keep_prob, is_training):
    """Base of the VGG-16 network (fully convolutional).

    Note that this function only constructs the network, and needs to be called
    with the correct variable scopes and tf.slim arg scopes.
    """
    a1 = slim.repeat(inputs, 2, slim.conv2d, 32, [3, 3], scope='conv1')
    a1 = slim.max_pool2d(a1, [2, 2], scope='pool1')
    a2 = slim.repeat(a1, 2, slim.conv2d, 64, [3, 3], scope='conv2')
    a2 = slim.max_pool2d(a2, [2, 2], scope='pool2')
    a3 = slim.repeat(a2, 3, slim.conv2d, 128, [3, 3], scope='conv3')
    a3 = slim.max_pool2d(a3, [2, 2], scope='pool3')
    a4 = slim.repeat(a3, 3, slim.conv2d, 256, [3, 3], scope='conv4')
    a4 = slim.max_pool2d(a4, [2, 2], scope='pool4')
    a5 = slim.repeat(a4, 3, slim.conv2d, 256, [3, 3], scope='conv5')
    a5 = slim.max_pool2d(a5, [2, 2], scope='pool5')

    a6 = slim.dropout(a5, dropout_keep_prob, is_training=is_training, scope='dropout7')

    a6  = slim.conv2d(a6, num_classes, [1, 1], activation_fn=None, normalizer_fn=None, scope='global_bottleneck')
    return a3, a4, a6


def _decoder(a3, a4, a6, output_resolution, num_classes, dropout_keep_prob, is_training):
    a7 = tf.image.resize_bilinear(images=a6, size=a4.get_shape()[1:3])
    skip_a4 = slim.conv2d(a4, num_classes, [1, 1], scope='skip_a4')
    a7 = a7 + skip_a4

    a8 = tf.image.resize_bilinear(images=a7, size=a3.get_shape()[1:3])
    skip_a3 = slim.conv2d(a3, num_classes, [1, 1], scope='skip_a3')
    a8 = a8 + skip_a3

    a9 = tf.image.resize_bilinear(images=a8, size=output_resolution)
    return skip_a4, skip_a3, a9


def _gvgg(inputs,
          num_classes=16,
          is_training=True,
          dropout_keep_prob=0.5,
          batch_norm_var_collection='moving_vars',
          scope='vgg_16'):
    """Slim convolutional VGG inspired network.

    Args:
      inputs: a tensor of size [batch_size, height, width, channels].
      num_classes: number of predicted classes (joints).
      is_training: whether or not the model is being trained.
      dropout_keep_prob: the probability that activations are kept in the dropout
        layers during training.
      scope: Optional scope for the variables.

    Returns:
      the last op containing the log predictions and end_points dict.
    """
    # We need to write notes about this.
    batch_norm_params = {
      # Decay for moving averages
      'decay': 0.9997,
      # epsilon to prevent 0s in variance
      'epsilon': 0.001,
      # Collection containing update_ops
      'updates_collections': ops.GraphKeys.UPDATE_OPS,
      # Collection containing the moving mean and moving variance.
      'variables_collections':{
          'beta': None,
          'gamma': None,
          'moving_mean': [batch_norm_var_collection],
          'moving_variance': [batch_norm_var_collection],
      },
      'is_training': is_training
    }

    with tf.variable_scope(scope, 'vgg_16', [inputs]) as sc:
        with tf.name_scope(scope):
            end_points_collection = sc.original_name_scope + '_end_points'
            # Collect outputs for conv2d, fully_connected and max_pool2d.
            with slim.arg_scope([slim.conv2d, slim.max_pool2d], outputs_collections=end_points_collection):
                # nested argscope because we don't apply activations and normalization to maxpool
                with slim.arg_scope([slim.conv2d],
                                    activation_fn=tf.nn.relu,
                                    normalizer_fn=slim.batch_norm,
                                    normalizer_params=batch_norm_params):
                    # 3-tuple (a3, a4, global_bottleneck)
                    a3, a4, a6 = _encoder(inputs,
                                          num_classes,
                                          dropout_keep_prob,
                                          is_training)

                    output_resolution = inputs.get_shape()[1:3]
                    _, _, logits = _decoder(a3,
                                            a4,
                                            a6,
                                            output_resolution,
                                            num_classes,
                                            dropout_keep_prob,
                                            is_training)


                    # Convert end_points_collection into a end_point dict.
                    end_points = slim.utils.convert_collection_to_dict(end_points_collection)

    return logits, end_points

def gvgg(inputs,
         num_classes=16,
         is_detector_training=True,
         is_regressor_training=True,
         dropout_keep_prob=0.5,
         batch_norm_var_collection='moving_vars',
         scope='gvgg'):
    """This is a wrapper around the `_vgg_16_bn_relu`, which assumes that we
    want to run VGG-16 with batchnorm and RELU as a single network pose
    estimator (i.e. just the detector).
    """
    return _gvgg(inputs=inputs,
                 num_classes=num_classes,
                 is_training=is_detector_training,
                 scope='gvgg')


def sampleGaussian(mu, log_sigma):
    """(Differentiably!) draw sample from Gaussian with given shape, subject to random noise epsilon"""
    with tf.name_scope("sample_gaussian"):
        # reparameterization trick
        epsilon = tf.random_normal(tf.shape(log_sigma), name="epsilon")
        return mu + epsilon * tf.exp(log_sigma) # N(mu, I * sigma**2)


def _get_reparameterization(hidden_enc):
    # latent distribution parameterized by hidden encoding
    # z ~ N(z_mean, np.exp(z_log_sigma)**2)
    params = slim.fully_connected(hidden_enc,
                                  LATENT_DIM*2,
                                  activation_fn=None,
                                  normalizer_fn=None,
                                  scope='z_mu')
    z_mu = params[:,:LATENT_DIM]
    z_log_sigma = params[:,LATENT_DIM:]

    return sampleGaussian(z_mu, z_log_sigma), z_mu, z_log_sigma


def _get_img_reconstruction(a3, a4, a6_shape, z_latent):
    batch_size = a3.get_shape().as_list()[0]

    d1 = tf.reshape(z_latent, [batch_size, 1, 1, LATENT_DIM])
    d2 = slim.conv2d_transpose(d1,6,[3,3]) # 64x64x6
    skip_a4 = slim.conv2d(a4, 6, [1, 1], scope='skip_a4') # 64x64x6
    d3 = tf.concat(skip_a4, d2, axis=3) # 64x64x12
    d4 = slim.conv2d_transpose(d3,3,[3,3]) #128x128x3
    skip_a3 = slim.conv2d(a4, 3,[3,3]) #128x128x3
    d2 += tf.concat(skip_a3, d3, axis=3) #128x128x6
    d3 = slim.conv2d_transpose(d2,3,[3,3]) #256x256x3

    print(d3.get_shape())
    img = slim.conv2d_transpose(d3,3,[3,3])
    return img


def _gvgg_vae(inputs,
          num_classes=16,
          is_training=True,
          dropout_keep_prob=0.5,
          batch_norm_var_collection='moving_vars',
          scope='gvgg_vae'):
    """Slim convolutional VGG inspired network.

    Args:
      inputs: a tensor of size [batch_size, height, width, channels].
      num_classes: number of predicted classes (joints).
      is_training: whether or not the model is being trained.
      dropout_keep_prob: the probability that activations are kept in the dropout
        layers during training.
      scope: Optional scope for the variables.

    Returns:
      the last op containing the log predictions and end_points dict.
    """
    # We need to write notes about this.
    batch_norm_params = {
      # Decay for moving averages
      'decay': 0.9997,
      # epsilon to prevent 0s in variance
      'epsilon': 0.001,
      # Collection containing update_ops
      'updates_collections': ops.GraphKeys.UPDATE_OPS,
      # Collection containing the moving mean and moving variance.
      'variables_collections':{
          'beta': None,
          'gamma': None,
          'moving_mean': [batch_norm_var_collection],
          'moving_variance': [batch_norm_var_collection],
      },
      'is_training': is_training
    }

    with tf.variable_scope(scope, 'gvgg_vae', [inputs]) as sc:
        with tf.name_scope(scope):
            end_points_collection = sc.original_name_scope + '_end_points'
            # Collect outputs for conv2d, fully_connected and max_pool2d.
            with slim.arg_scope([slim.conv2d, slim.max_pool2d, slim.conv2d_transpose, slim.fully_connected, slim.flatten], outputs_collections=end_points_collection):
                # nested argscope because we don't apply activations and normalization to maxpool
                with slim.arg_scope([slim.conv2d, slim.conv2d_transpose, slim.fully_connected],
                                    activation_fn=tf.nn.relu,
                                    normalizer_fn=slim.batch_norm,
                                    normalizer_params=batch_norm_params):
                    # 3-tuple (a3, a4, global_bottleneck)
                    a3, a4, a7 = _encoder(inputs,
                                          num_classes,
                                          dropout_keep_prob,
                                          is_training)

                    z = slim.flatten(a7)
                    # We need the shape in int format to pass to nn ops
                    a7_shape = a7.get_shape().as_list()[1]
                    z_latent, z_mu, z_log_sigma = _get_reparameterization(z)

                    recon = _get_img_reconstruction(a3, a4, a7_shape, z_latent)
                    output_resolution = inputs.get_shape()[1:3]
                    _, _, logits = _decoder(a3,
                                            a4,
                                            a7,
                                            output_resolution,
                                            num_classes,
                                            dropout_keep_prob,
                                            is_training)
                    # Convert end_points_collection into a end_point dict.
                    end_points = slim.utils.convert_collection_to_dict(end_points_collection)

                    # For calculating the KL loss
                    end_points['recon'] = recon
                    end_points['z_mu'] = z_mu
                    end_points['z_log_sigma'] = z_log_sigma
                    end_points['pose_logits'] = pose_logits

    return logits, end_points


def gvgg_vae(inputs,
         num_classes=16,
         is_detector_training=True,
         is_regressor_training=True,
         dropout_keep_prob=0.5,
         batch_norm_var_collection='moving_vars',
         scope='gvgg'):
    """This is a wrapper around the `_vgg_16_bn_relu`, which assumes that we
    want to run VGG-16 with batchnorm and RELU as a single network pose
    estimator (i.e. just the detector).
    """
    return _gvgg_vae(inputs=inputs,
                     num_classes=num_classes,
                     is_training=is_detector_training,
                     scope='gvgg')


def gvgg_cascade(inputs,
                 num_classes=16,
                 is_detector_training=True,
                 is_regressor_training=True,
                 scope='gvgg_cascade'):
    """The cascade of VGG-style networks from the Bulat paper."""
    detect_logits, detect_endpoints = _gvgg(inputs=inputs,
                                            num_classes=num_classes,
                                            is_training=is_detector_training,
                                            scope='gvgg_cascade')

    detect_endpoints['detect_logits'] = detect_logits

    stacked_heatmaps = tf.concat(values=[detect_logits, inputs], axis=3)

    regression_logits, _ = _gvgg(inputs=stacked_heatmaps,
                                 num_classes=num_classes,
                                 is_training=is_regressor_training,
                                 scope=scope)

    return regression_logits, detect_endpoints

# TODO (Thor)
def gvgg_resnet_cascade(inputs,
                        num_classes=16,
                        is_detector_training=True,
                        is_regressor_training=True,
                        scope='gvgg_resnet_cascade'):
    """The cascade of VGG-style networks from the Bulat paper."""
    detect_logits, detect_endpoints = _gvgg(inputs=inputs,
                                            num_classes=num_classes,
                                            is_training=is_detector_training,
                                            scope='vgg_16')

    detect_endpoints['detect_logits'] = detect_logits

    stacked_heatmaps = tf.concat(values=[detect_logits, inputs], axis=3)
    #regression_logits, _ = resnet.(inputs=stacked_heatmaps,
    #                                     num_classes=num_classes,
    #                                     is_training=is_regressor_training,
    #                                     scope=scope)
    #
    #return regression_logits, detect_endpoints
    pass
