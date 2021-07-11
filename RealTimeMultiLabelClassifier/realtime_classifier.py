import matplotlib
import matplotlib.pyplot as plt
import os
import random
import zipfile
import io
import scipy.misc
import numpy as np
import glob
import imageio
import tensorflow as tf
from six import BytesIO
from PIL import Image, ImageDraw, ImageFont
from IPython.display import display, Javascript
from IPython.display import Image as IPyImage
from DeepLearningUtilities.progress_bar import progress_bar
from object_detection.utils import label_map_util
from object_detection.utils import config_util
from object_detection.utils import visualization_utils as viz_utils
from object_detection.builders import model_builder


def load_image(path):
    img_data = tf.io.gfile.GFile(path, 'rb').read()
    image = Image.open(BytesIO(img_data))
    (w, h) = image.size
    return np.array(image.getdata()).reshape((h, w, 3)).astype(np.uint8)


def plot_detections(image_np, boxes, classes, scores, category_index, image_name=None):
    image_np_with_annotations = image_np.copy()

    viz_utils.visualize_boxes_and_labels_on_image_array(
        image_np_with_annotations,
        boxes,
        classes,
        scores,
        category_index,
        use_normalized_coordinates=True,
        min_score_thresh=0.8)

    if image_name:
        plt.imsave(image_name, image_np_with_annotations)
    else:
        plt.imshow(image_np_with_annotations)


class RealTimeClassifier:
    def __init__(self, t_path='./dataset_realtime/train',
                 a_path='./dataset_realtime/annotations',
                 pipeline_config='./SSD_retinanet_config/ssd_resnet50_v1_fpn_1024x1024_coco17_tpu-8.config',
                 checkpoint_path='./RealTimeMultiLabelClassifier/checkpoints/ckpt-0'):
        self.checkpoint_path = checkpoint_path
        self.pipeline_config = pipeline_config
        self.train_path = t_path
        self.annotations_path = a_path
        self.model = None
        self.num_classes = 8
        self.category_index = None
        self.train_images = []

        # Data tensors initialization in the constructor
        self.train_images_tensors = []
        self.train_gt_classes_one_hot_tensors = []
        self.train_gt_bbox_tensors = []

    def set_up_data(self):
        # Load all the training images inside the list class attribute
        train_annotations = []
        images = os.listdir(self.train_path)
        sorted_images = map(lambda l: int(l[:-4]), images)
        sorted_images = list(sorted_images)
        total = len(sorted_images)
        sorted_images.sort()
        sorted_images = map(lambda l: str(l) + '.jpg', sorted_images)

        it = 0
        if total != 0:
            progress_bar(it, total, prefix='Train images loading: ', suffix='Complete', length=50)
        for im in sorted_images:
            img_path = os.path.join(self.train_path, im)
            print(img_path)
            self.train_images.append(load_image(img_path))
            it += 1
            progress_bar(it, total, prefix=str(img_path) + ' successfully loaded: ', suffix='Complete', length=50)

        print('Images loaded successfully =>', str(len(self.train_images)), 'train images loaded!')

        labels_list = os.listdir(self.annotations_path)
        sorted_labels = map(lambda l: int(l[:-4]), labels_list)
        sorted_labels = list(sorted_labels)
        total = len(sorted_labels)
        sorted_labels.sort()
        sorted_labels = map(lambda l: str(l) + '.txt', sorted_labels)

        it = 0
        if total != 0:
            progress_bar(it, total, prefix='Train labels loading: ', suffix='Complete', length=50)
        for labels_fn in sorted_labels:
            labels = open(self.annotations_path + '/' + labels_fn, "r")
            actual_labels_tensor = []
            for label in labels:
                c, x, y, w, h = map(float, label.split())
                c = int(c) + 1
                y1 = float(x - w / 2)
                y2 = float(x + w / 2)
                x1 = float(y - h / 2)
                x2 = float(y + h / 2)
                actual_labels_tensor.append(([x1, y1, x2, y2], c))
            train_annotations.append(actual_labels_tensor)
            it += 1
            progress_bar(it, total, prefix=str(labels_fn) + ' successfully loaded: ', suffix='Complete', length=50)

        print('Annotations loaded successfully =>', str(len(train_annotations)), 'train annotations loaded!')

        self.category_index = {1: {'id': 1,
                                   'name': 'car'},
                               2: {'id': 2,
                                   'name': 'moto'},
                               3: {'id': 3,
                                   'name': 'truck'},
                               4: {'id': 4,
                                   'name': 'pedestrian'},
                               5: {'id': 5,
                                   'name': 'forbid_signal'},
                               6: {'id': 6,
                                   'name': 'warning_signal'},
                               7: {'id': 7,
                                   'name': 'stop_signal'},
                               8: {'id': 8,
                                   'name': 'yield_signal'}
                               }

        print('Category Index initialized successfully!')

        it = 0
        total = len(self.train_images)
        if total != 0:
            progress_bar(it, total, prefix='Training tensors creation: ', suffix='Complete', length=50)
        for (train_image, train_annotation) in zip(self.train_images, train_annotations):
            # Unbox the train annotation tuples for handling bounding boxes and their classes tensors separately
            image_bounding_boxes = []
            image_classes = []
            for a in train_annotation:
                bbox, cl = a
                image_bounding_boxes.append(bbox)
                image_classes.append(cl)

            image_bounding_boxes = np.array(image_bounding_boxes)
            image_classes = np.array(image_classes)

            # Convert training images to tensors adding a batch dimension
            self.train_images_tensors.append(
                tf.expand_dims(tf.convert_to_tensor(train_image, dtype=tf.float32), axis=0))

            # Convert array of bounding boxes to tensors and separate them from their classes
            self.train_gt_bbox_tensors.append(tf.convert_to_tensor(image_bounding_boxes, dtype=tf.float32))

            # Offset for taking care of zero-indexed ground truth classes
            zero_indexed_classes = tf.convert_to_tensor(image_classes - 1, dtype=np.int32)

            # do one-hot encoding to ground truth classes
            self.train_gt_classes_one_hot_tensors.append(tf.one_hot(zero_indexed_classes, self.num_classes))

            it += 1
            progress_bar(it, total, prefix='Training tensors creation: ', suffix='Complete', length=50)

        print('Finished setting up data => DATA READY FOR TRAINING!!!')

    def compile_model(self):
        config = config_util.get_configs_from_pipeline_file(self.pipeline_config)
        model_config = config['model']

        # Set number of classes to 8: cars, motos, trucks, pedestrians, forbid_signals, warning_signals, stop_signals and yield_signals
        model_config.ssd.num_classes = self.num_classes

        # Freeze Batch Normalization layers because the objective is to fine tune the network, not to retrain all the params
        # This will reduce training time and it's good for small size retrain batches (the network will learn better based on the previous knowledge)
        model_config.ssd.freeze_batchnorm = True

        # Building the model based on the model_config that was extracted before form the pipeline_config
        # Set training to true needed as the scope is to fine tune the network with new custom dataset
        self.model = model_builder.build(model_config=model_config, is_training=True)

        '''
        Final target: create a custom model which reuses some parts of the layers of RetinaNet model.
        The interesting parts of RetinaNet that are needed for fine tuning are the feature extraction layers and the 
        bounding box regression pred layers: <_feature_extractor> and <_box_predictor>
        The part of the network that will be ignored is the classification prediction layer because I am going to define
        a new classification based on the real time road detection project.
        '''
        # Inside box predictor modules of the network it is necessary to identify the variables in that are not going to
        # be restored from the checkpoint and in this way they will be fine tuned automatically, so they are listed now
        '''
        _base_tower_layers_for_heads: contains the layers for the prediction before the final bounding box prediction
        and the layers for the prediction before the final class prediction.
        
        _box_prediction_head: points to the bounding box prediction layer.
        
        _prediction_heads: is a dict that refers to both the layer that predicts bounding boxes and the layer that 
        predicts the class. As I am going to retrain this layers I won't load checkpoints for this.
        
        The target is to isolate this layers for retrain with the custom dataset
        '''
        box_predictor_checkpoint = tf.train.Checkpoint(
            _base_tower_layers_for_heads=self.model._box_predictor._base_tower_layers_for_heads,
            _box_prediction_head=self.model._box_predictor._box_prediction_head,
        )
        model_checkpoint = tf.train.Checkpoint(
            _feature_extractor=self.model._feature_extractor,
            _box_predictor=box_predictor_checkpoint)
        checkpoint = tf.train.Checkpoint(model=model_checkpoint)

        # Restore the checkpoint to the checkpoint path
        # !!! expect_partial() is needed for not caring about warnings when checkpoint restoring !!!
        checkpoint.restore(self.checkpoint_path).expect_partial()

        # Finally it is necessary to run a dummy image to generate the model variables that are 0 when restoring
        # from a checkpoint file but it is correctly initialized with the corresponding weights with the first image
        # prediction

        # Model preprocess() method can be used to generate a dummy image with it's shapes
        dummy_image, dummy_shape = self.model.preprocess(tf.zeros([1, 1024, 1024, 3]))

        # Run a prediction with the dummy image and shape
        prediction = self.model.predict(dummy_image, dummy_shape)

        # Model postprocess() method is used to transform the predictions into final detections
        detections = self.model.postprocess(prediction, dummy_shape)

        print('Model weights restored successfully!')

        # Alert assertion to provide feedback to the user
        assert len(self.model.trainable_variables) > 0, 'Please pass in a dummy image to create the trainable variables'

    # Decorator @tf.function for faster training in graph mode
    @tf.function
    def train_step(self, image_list, gt_boxes, gt_classes, optimizer, fine_tune_variables):
        with tf.GradientTape() as tape:

            # Preprocess the images
            preprocessed_image_list = []
            true_shape_list = []

            for img in image_list:
                processed_img, true_shape = self.model.preprocess(img)
                preprocessed_image_list.append(processed_img)
                true_shape_list.append(true_shape)

            preprocessed_image_tensor = tf.concat(preprocessed_image_list, axis=0)
            true_shape_tensor = tf.concat(true_shape_list, axis=0)

            # Make a prediction
            prediction = self.model.predict(preprocessed_image_tensor, true_shape_tensor)

            # Provide the ground truth to the model for the loss to execute correctly
            self.model.provide_groundtruth(
                groundtruth_boxes_list=gt_boxes,
                groundtruth_classes_list=gt_classes)

            # Calculate the total loss localization loss + classification loss
            losses = self.model.loss(prediction, true_shape_tensor)
            total_loss = losses['Loss/localization_loss'] + losses['Loss/classification_loss']

            # Calculate the gradients
            gradients = tape.gradient(total_loss, fine_tune_variables)

            # Optimize selected variables
            optimizer.apply_gradients(zip(gradients, fine_tune_variables))

        return total_loss

    def train(self, b_size=20, lr=0.001):
        # Initialize custom training hyper-parameters
        batch_size = b_size
        num_batches = len(os.listdir(self.train_path)) // b_size  # Total number of train images divided by batch size
        learning_rate = lr
        optimizer = tf.keras.optimizers.SGD(learning_rate=learning_rate, momentum=0.9)

        # Selecting layers to perform fine tuning on them
        # In RetinaNet layers with "tower" in it's names refers to layers placed before the prediction layer
        # And layers with "head" in it's names refers to the prediction layers

        # Now it's time to define a list that contains the layers that will be fine tuned
        trainable_variables = self.model.trainable_variables
        to_fine_tune = []
        prefixes_to_train = [
            'WeightSharedConvolutionalBoxPredictor/WeightSharedConvolutionalBoxHead',
            'WeightSharedConvolutionalBoxPredictor/WeightSharedConvolutionalClassHead']
        for trainable_variable in trainable_variables:
            if any([trainable_variable.name.startswith(prefix) for prefix in prefixes_to_train]):
                to_fine_tune.append(trainable_variable)

        # Training loop
        print('Starting fine tuning...', flush=True)

        for idx in range(num_batches):
            # Grab keys for a random subset of examples
            all_idx = list(range(len(self.train_images)))
            random.shuffle(all_idx)
            rng_selected_idx = all_idx[:batch_size]

            # Get the actual ground truth bounding boxes and classes
            gt_boxes_list = [self.train_gt_bbox_tensors[idx] for idx in rng_selected_idx]
            gt_classes_list = [self.train_gt_classes_one_hot_tensors[idx] for idx in rng_selected_idx]

            # Get the images
            image_tensors = [self.train_images_tensors[idx] for idx in rng_selected_idx]

            # Training step
            total_loss = self.train_step(image_tensors, gt_boxes_list, gt_classes_list, optimizer, to_fine_tune)

            # Show the model loss after training in actual batch
            print('batch ' + str(idx) + ' of ' + str(num_batches) + ', loss=' + str(total_loss.numpy()), flush=True)

        print('Finished fine tuning!')

        # Save the model
        self.model.save('./last_results/last_models/fine_tuned_realtime_retinanet50.h5')

    def load_model(self):
        self.model = tf.keras.models.load_model('./last_results/last_models/fine_tuned_realtime_retinanet50.h5')

    def predict(self):
        print('Not yet implemented')