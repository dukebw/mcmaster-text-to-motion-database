import os
import threading
import math
import numpy as np
import tensorflow as tf
from pose_utils import pose_util
from dataset.mpii_read import mpii_read
from dataset.mpii_datatypes import Person
from pose_utils.timethis import timethis
from dataset.shapes import Point, Rectangle
from pose_utils.sparse_to_dense import sparse_joints_to_dense_single_example

FLAGS = tf.app.flags.FLAGS

tf.app.flags.DEFINE_string(
    'mpii_filepath',
    '/mnt/data/datasets/MPII_HumanPose/raw_data/mpii_human_pose_v1_u12_2/mpii_human_pose_v1_u12_1.mat',
    """Filepath to the .mat file from the MPII HumanPose
    [website](human-pose.mpi-inf.mpg.de)""")

tf.app.flags.DEFINE_string('train_dir', '/mnt/data/datasets/MPII_HumanPose/train_512px',
                            """Path in which to write the TFRecord files.""")

tf.app.flags.DEFINE_boolean('is_train', True,
                            """Write training (True) or test (False)
                            TFRecords?""")

tf.app.flags.DEFINE_integer('num_threads', 4,
                            """Number of threads to use to write TF Records""")

tf.app.flags.DEFINE_integer('train_shards', 80,
                            """Number of output shards (TFRecord files
                            containing training examples) to create.""")

tf.app.flags.DEFINE_integer('image_dim', 512,
                            """Dimension of the square image to output.""")

class ImageCoder(object):
    """A class that holds a session, passed using dependency injection during
    `ImageCoder` instantiations, which is used to run a TF graph to decode JPEG
    images.

    On initialization, a graph is set up containing the following operations:
        1. Decode an input JPEG image.
        2. Crop the raw image to an input bounding box, e.g. the box around a
           person.
        3. Pad the shorter dimension with a black border, to a square size.
        4. Resize the now square image to FLAGS.image_dim*FLAGS.image_dim.
        5. Encode the cropped, resized image as JPEG.
    """
    def __init__(self, session):
        self._sess = session

        self._decode_jpeg_data = tf.placeholder(dtype=tf.string)
        raw_image = tf.image.decode_jpeg(contents=self._decode_jpeg_data, channels=3)
        self._img_shape = tf.shape(input=raw_image)

        self._crop_height_offset = tf.placeholder(dtype=tf.int32)
        self._crop_width_offset = tf.placeholder(dtype=tf.int32)
        self._crop_height = tf.placeholder(dtype=tf.int32)
        self._crop_width = tf.placeholder(dtype=tf.int32)
        self._height_pad = tf.placeholder(dtype=tf.int32)
        self._width_pad = tf.placeholder(dtype=tf.int32)
        self._padded_img_dim = tf.placeholder(dtype=tf.int32)

        cropped_img = tf.image.crop_to_bounding_box(image=raw_image,
                                                    offset_height=self._crop_height_offset,
                                                    offset_width=self._crop_width_offset,
                                                    target_height=self._crop_height,
                                                    target_width=self._crop_width)

        pad_image = tf.image.pad_to_bounding_box(image=cropped_img,
                                                 offset_height=self._height_pad,
                                                 offset_width=self._width_pad,
                                                 target_height=self._padded_img_dim,
                                                 target_width=self._padded_img_dim)

        self._scaled_image_tensor = tf.cast(
            tf.image.resize_images(images=pad_image, size=[FLAGS.image_dim, FLAGS.image_dim]),
            tf.uint8)

        self._scaled_image_jpeg = tf.image.encode_jpeg(image=self._scaled_image_tensor)

        self._x_joints = tf.placeholder(dtype=tf.float32)
        self._y_joints = tf.placeholder(dtype=tf.float32)
        self._joint_indices = tf.placeholder(dtype=tf.int64)
        self._joints_shape = tf.placeholder(dtype=tf.int64)
        sparse_x_joints = tf.SparseTensor(indices=self._joint_indices,
                                          values=self._x_joints,
                                          dense_shape=self._joints_shape)
        sparse_y_joints = tf.SparseTensor(indices=self._joint_indices,
                                          values=self._y_joints,
                                          dense_shape=self._joints_shape)
        sparse_joint_indices = tf.SparseTensor(indices=self._joint_indices,
                                               values=self._joint_indices,
                                               dense_shape=self._joints_shape)
        x_dense_joints, y_dense_joints, _, _ = sparse_joints_to_dense_single_example(
            sparse_x_joints, sparse_y_joints, sparse_joint_indices, Person.NUM_JOINTS)

        self._binary_maps = _get_binary_maps(FLAGS.image_dim, x_dense_joints, y_dense_joints)

    def decode_jpeg(self, image_data):
        """Returns the shape of an input JPEG image.

        Args:
            image_data: A JPEG image to find the shape of.

        Returns:
            shape: Shape of the image in the format Point(width, height).
        """
        shape = self._sess.run(fetches=self._img_shape,
                               feed_dict={self._decode_jpeg_data: image_data})
        assert len(shape) == 3
        assert shape[2] == 3

        return Point(shape[1], shape[0])

    def scale_encode(self,
                     image_data,
                     crop_offsets,
                     crop_dim,
                     padding,
                     padded_dim):
        """Runs the entire sequence of decode -> crop -> pad -> resize ->
        encode JPEG, and returns the resultant JPEG image.

        Args:
            image_data: JPEG image data.
            crop_offsets: A `Point` containing the offset in the original image
                of the sub-image to crop to.
            crop_dim: A `Point` containing the width and height of the cropped
                section, in the format Point(width, height).
            padding: Amount of padding to do on the cropped image in the format
                Point(x_padding, y_padding).
            padded_dim: Length of the edge length of the square padded image.

        Returns: The image cropped and padded to the given bounding box, scaled
            to FLAGS.image_dim*FLAGS.image_dim, and encoded as JPEG.
        """
        feed_dict = {
            self._decode_jpeg_data: image_data,
            self._crop_height_offset: crop_offsets.y,
            self._crop_width_offset: crop_offsets.x,
            self._crop_height: crop_dim.y,
            self._crop_width: crop_dim.x,
            self._height_pad: padding.y,
            self._width_pad: padding.x,
            self._padded_img_dim: padded_dim
        }

        scaled_img_jpeg = self._sess.run(fetches=self._scaled_image_jpeg, feed_dict=feed_dict)

        return scaled_img_jpeg

    def get_binary_maps(self, x_joints, y_joints, joint_indices, joints_shape):
        """Runs the binary-map generation part of the graph stored in this
        ImageCoder instance.
        """
        feed_dict = {
            self._x_joints: x_joints,
            self._y_joints: y_joints,
            self._joint_indices: joint_indices,
            self._joints_shape: joints_shape
        }

        return self._sess.run(fetches=self._binary_maps, feed_dict=feed_dict)


def _clamp_range(value, min_val, max_val):
    """Clamps value to the range [min_val, max_val].

    Args:
        value: A number to be clamped.
        min_val: Minimum value to return.
        max_val: Maximum value to return.

    Return:
        value if value is in [min_val, max_val], otherwise whichever of
        `min_val` or `max_val` is closer to value.
    """
    return max(min_val, min(value, max_val))


def _clamp_point_to_image(point, image_shape):
    """Clamps `point` so that it is inside the image whose shape is given by
    `image_shape`.

    Args:
        point: `Point` to clamp.
        image_shape: Dimensions of an image in the format Point(width, height).

    Returns:
        clamped_point: `point` with its dimensions clamped to the edges of the
            image.
    """
    clamped_point = (_clamp_range(point.x, 0, image_shape.x),
                     _clamp_range(point.y, 0, image_shape.y))

    return clamped_point


def _bytes_feature(value):
    """Wrapper for inserting bytes feature into Example proto"""
    return tf.train.Feature(bytes_list=tf.train.BytesList(value=[value]))


def _float_feature(value):
    """Wrapper for inserting FloatList feature into Example proto"""
    if not isinstance(value, list):
        value = [value]

    return tf.train.Feature(float_list=tf.train.FloatList(value=value))


def _int64_feature(value):
    """Wrapper for inserting Int64 features into Example proto"""
    if not isinstance(value, list):
        value = [value]

    return tf.train.Feature(int64_list=tf.train.Int64List(value=value))


def _append_scaled_joint(joints, joint_dim, max_joint_dim, image_center):
    """Appends to joints the value of joint_dim, scaled down to be in the range
    [-0.5, 0.5].

    Args:
        joints: List of joints, in the order [x0, y0, x1, y1, ...]
        joint_dim: Next either xi or yi to append.
        max_joint_dim: Maximum dimension of the image in which the joint
            appears, e.g. height of 1080 in a 1920x1080 image.
    """
    scaled_joint = _clamp_range(
        (joint_dim - image_center)/max_joint_dim, -0.5, 0.5)
    joints.append(scaled_joint)


def _extract_labeled_joints(person_joints,
                            cropped_img_shape,
                            padding,
                            offsets):
    """Extracts all of the joints for a single person in image, and puts them
    in a list in the format [x0, y0, x1, y1, ...].

    Not all joints are labeled for each person, so this function also returns a
    sparse list of indices for person, where each index indicates which joint
    is labeled. The indices correspond to two sparse lists of joint
    coordinates, one for x and one for y.

    Args:
        person_joints: Joints of person in the image.
        cropped_img_shape: The shape of the given image post-cropping, in the
            format Point(cols, rows).
        padding: Pixels of padding in Point(width, height) dimensions.
        offsets: The Point(width, height) offsets of the top left corner of
            this cropped image in the original image. This is needed to
            translate the joint labels.

    Returns:
        (x_sparse_joints, y_sparse_joints, sparse_joint_indices) tuple, where
        `*_sparse_joints` are lists of x and y joint coordinates, and
        sparse_joint_indices is a list of indices indicate which joints are
        which.

        Visually: `x_sparse_joints` [x0, x1, x2]
                  `y_sparse_joints` [y0, y1, y2]
                  `sparse_joint_indices` [0, 1, 3]

                  The above corresponds to a person for whom (x0, y0), (x1, y1)
                  and (x2, y2) are joints 0, 1 and 3 (indexed 0-15 in (x, y)
                  pairs as in the MPII dataset) for `person`, respectively.
    """
    x_sparse_joints = []
    y_sparse_joints = []
    sparse_joint_indices = []
    is_visible_list = []
    max_cropped_img_dim = max(cropped_img_shape.x, cropped_img_shape.y)
    abs_image_center = Point(offsets.x + cropped_img_shape.x/2,
                             offsets.y + cropped_img_shape.y/2)

    for joint_index in range(len(person_joints)):
        joint = person_joints[joint_index]
        if joint is not None:
            if ((offsets.x <= joint.x <= (offsets.x + cropped_img_shape.x)) and
                (offsets.y <= joint.y <= (offsets.y + cropped_img_shape.y))):
                _append_scaled_joint(x_sparse_joints,
                                     joint.x,
                                     max_cropped_img_dim,
                                     abs_image_center.x)
                _append_scaled_joint(y_sparse_joints,
                                     joint.y,
                                     max_cropped_img_dim,
                                     abs_image_center.y)

                sparse_joint_indices.append(joint_index)

                is_visible_list.append(joint.is_visible)

    return x_sparse_joints, y_sparse_joints, sparse_joint_indices, is_visible_list


def _find_person_bounding_box(person, img_shape):
    """Finds an enclosing bounding box for `person` in the image with
    `img_shape` dimensions.

    Currently the bounding box is found by taking the
    (scale*200px) by (scale*200px) rectangle centered around `objpos`.

    One experiment would be to take the bounding box found with the current
    method, and expand or shrink each dimension to the minimum spanning
    rectangle such that all the labelled joints are contained.

    Args:
        person: Person to find bounding box for.
        img_shape: Dimensions of the image that the person is in.

    Returns:
        A `Rectangle` describing the box bounding `person`.
    """
    x = person.objpos.x
    y = person.objpos.y

    # NOTE(brendan): The MPII `scale` is with respect to 200px object height,
    # and `objpos` is at the person's center.
    person_half_dim = 100*person.scale
    top_left = Point(x - person_half_dim, y - person_half_dim)
    bottom_right = Point(x + person_half_dim, y + person_half_dim)

    return Rectangle(_clamp_point_to_image(top_left, img_shape) +
                     _clamp_point_to_image(bottom_right, img_shape))


def _find_padded_person_dim(person_rect):
    """Finds the large dimension, shape and padding needed for the bounding box
    around a person.

    Args:
        person_rect: A `Rectangle` describing the bounding box of a person.

    Returns:
        padded_img_dim: Larger dimension of the person's bounding box.
        person_shape_xy: Point(width, height) describing the person's
            bounding box dimensions.
        padding_xy: Point(padding_x, padding_y) describing the padding for the
            person in the x and y dimensions, at least one of which will be
            zero.
    """
    person_width = person_rect.get_width()
    person_height = person_rect.get_height()
    padding = abs(person_height - person_width)/2
    if person_height > person_width:
        height_pad = 0
        width_pad = padding
    else:
        height_pad = padding
        width_pad = 0

    padded_img_dim = max(person_width, person_height)
    person_shape_xy = Point(person_width, person_height)
    padding_xy = Point(width_pad, height_pad)

    return padded_img_dim, person_shape_xy, padding_xy


def _get_binary_maps(image_dim, x_dense_joints, y_dense_joints):
    """
    Creates binary maps of shape [image_dim, image_dim, Person.NUM_JOINTS],
    that are 10 pixels in radius.
    """
    dim_j = complex(0, image_dim)
    y, x = np.mgrid[-0.5:0.5:dim_j, -0.5:0.5:dim_j]
    y = y.astype(np.float32)
    x = x.astype(np.float32)
    y = tf.expand_dims(input=y, axis=-1)
    y = tf.tile(input=y, multiples=[1, 1, Person.NUM_JOINTS])
    x = tf.expand_dims(input=x, axis=-1)
    x = tf.tile(input=x, multiples=[1, 1, Person.NUM_JOINTS])

    binary_maps = ((y - y_dense_joints)**2 + (x - x_dense_joints)**2 < (10/image_dim)**2)

    return tf.cast(binary_maps, tf.uint8)


def _write_example(coder, image_jpeg, people_in_img, writer):
    """Writes an example to the TFRecord file owned by `writer`.

    See `_extract_labeled_joints` for the format of `*_sparse_joints` and
    `sparse_joint_indices`.
    """
    img_shape = coder.decode_jpeg(image_jpeg)

    for person in people_in_img:
        person_rect = _find_person_bounding_box(person, img_shape)

        padded_img_dim, person_shape_xy, padding_xy = _find_padded_person_dim(
            person_rect)

        scaled_img_jpeg = coder.scale_encode(
            image_jpeg,
            person_rect.top_left,
            person_shape_xy,
            padding_xy,
            padded_img_dim)

        labels = _extract_labeled_joints(
            person.joints,
            person_shape_xy,
            padding_xy,
            person_rect.top_left)

        x_joints, y_joints, joint_indices, is_visible_list = labels

        joints_shape = len(joint_indices)
        binary_maps = coder.get_binary_maps(x_joints,
                                            y_joints,
                                            np.reshape(joint_indices, [joints_shape, 1]),
                                            [joints_shape])

        head_rect_width = person.head_rect.get_width()/padded_img_dim
        head_rect_height = person.head_rect.get_height()/padded_img_dim
        head_size = 0.6*math.sqrt(head_rect_width**2 + head_rect_height**2)

        example = tf.train.Example(
            features=tf.train.Features(
                feature={
                    'image_jpeg': _bytes_feature(scaled_img_jpeg),
                    'binary_maps': _bytes_feature(np.ndarray.tobytes(binary_maps)),
                    'joint_indices': _int64_feature(joint_indices),
                    'x_joints': _float_feature(x_joints),
                    'y_joints': _float_feature(y_joints),
                    'is_visible_list': _int64_feature(is_visible_list),
                    'head_size': _float_feature(head_size)
                }))
        writer.write(tf.compat.as_bytes(example.SerializeToString()))


def _process_image_files_single_thread(coder, thread_index, ranges, mpii_dataset):
    """Processes a range of filenames and labels in the MPII dataset
    corresponding to the given thread index.

    Args:
        coder: An instance of `ImageCoder`, which is used to decode JPEG images
            from MPII.
        thread_index: Index of the current thread (must be unique).
        mpii_dataset: Instance of `MpiiDataset` containing data shuffled in the
            order that those data should be written to TF Record.
    """
    if FLAGS.is_train:
        base_name = 'train'
    else:
        base_name = 'test'

    shards_per_thread = FLAGS.train_shards/FLAGS.num_threads
    shard_ranges = pose_util.get_n_ranges(ranges[thread_index][0],
                                          ranges[thread_index][1],
                                          shards_per_thread)

    for shard_index in range(len(shard_ranges)):
        tfrecord_index = int(thread_index*shards_per_thread + shard_index)
        tfrecord_filename = '{}{}.tfrecord'.format(base_name, tfrecord_index)
        tfrecord_filepath = os.path.join(FLAGS.train_dir, tfrecord_filename)

        options = tf.python_io.TFRecordOptions(
            compression_type=tf.python_io.TFRecordCompressionType.ZLIB)
        with tf.python_io.TFRecordWriter(path=tfrecord_filepath, options=options) as writer:
            shard_start = shard_ranges[shard_index][0]
            shard_end = shard_ranges[shard_index][1]
            for img_index in range(shard_start, shard_end):
                with tf.gfile.FastGFile(name=mpii_dataset.img_filenames[img_index], mode='rb') as f:
                    image_jpeg = f.read()

                _write_example(coder,
                               image_jpeg,
                               mpii_dataset.people_in_imgs[img_index],
                               writer)


def _process_image_files(mpii_dataset, num_examples, session):
    """Processes the image files in `mpii_dataset`, using multiple threads to
    write the data to TF Records on disk.
    """
    # TODO(brendan): Better documentation about `Coordinator`
    coord = tf.train.Coordinator()

    num_threads = FLAGS.num_threads

    ranges = pose_util.get_n_ranges(0, num_examples, num_threads)

    coder = ImageCoder(session)

    threads = []
    for thread_index in range(num_threads):
        args = (coder, thread_index, ranges, mpii_dataset)
        t = threading.Thread(target=_process_image_files_single_thread, args=args)
        t.start()
        threads.append(t)

    coord.join(threads)


@timethis
def write_tf_record(mpii_dataset, num_examples=None):
    # TODO(brendan): Docstring...
    assert ((FLAGS.train_shards % FLAGS.num_threads) == 0)

    if not os.path.exists(FLAGS.train_dir):
        os.mkdir(FLAGS.train_dir)

    with tf.Graph().as_default():
        with tf.Session() as session:
            if num_examples == None:
                num_examples = len(mpii_dataset.img_filenames)

            _process_image_files(mpii_dataset, num_examples, session)


def main(argv=None):
    """Usage:
    ('python3 -m write_tf_record
     --mpii_filepath "/mnt/data/datasets/MPII_HumanPose/mpii_human_pose_v1_u12_2/mpii_human_pose_v1_u12_1.mat"
     --is_train True
     --num_threads 3')

     Type 'python3 -m write_tf_record --help' for options.
    """
    mpii_dataset = mpii_read(FLAGS.mpii_filepath, FLAGS.is_train)
    write_tf_record(mpii_dataset)


if __name__ == "__main__":
    tf.app.run()
