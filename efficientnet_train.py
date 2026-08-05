import os
import json
import tensorflow as tf
from tensorflow.keras.applications import EfficientNetB0
from tensorflow.keras.layers import Dense, Dropout, GlobalAveragePooling2D
from tensorflow.keras.models import Model
from tensorflow.keras.callbacks import (
    ModelCheckpoint,
    EarlyStopping,
    ReduceLROnPlateau
)

from sklearn.utils.class_weight import compute_class_weight
import numpy as np

# ----------------------------
# НАСТРОЙКИ
# ----------------------------

DATASET_PATH = "dataset"

IMAGE_SIZE = (224, 224)

BATCH_SIZE = 16

EPOCHS = 50

MODEL_NAME = "paithology_efficientnet.keras"

CLASS_FILE = "class_names.json"

# ----------------------------
# ЗАГРУЗКА ДАННЫХ
# ----------------------------

train_dataset = tf.keras.utils.image_dataset_from_directory(
    DATASET_PATH,
    validation_split=0.2,
    subset="training",
    seed=42,
    image_size=IMAGE_SIZE,
    batch_size=BATCH_SIZE,
    shuffle=True,
    label_mode="categorical"
)

validation_dataset = tf.keras.utils.image_dataset_from_directory(
    DATASET_PATH,
    validation_split=0.2,
    subset="validation",
    seed=42,
    image_size=IMAGE_SIZE,
    batch_size=BATCH_SIZE,
    shuffle=True,
    label_mode="categorical"
)

class_names = train_dataset.class_names

labels = []

for _, y in train_dataset.unbatch():
    labels.append(np.argmax(y.numpy()))

labels = np.array(labels)

class_weights = compute_class_weight(
    class_weight="balanced",
    classes=np.arange(len(class_names)),
    y=labels
)

class_weights = {
    i: float(w)
    for i, w in enumerate(class_weights)
}

print("\nВес каждого класса:")

for i, w in class_weights.items():
    print(class_names[i], "->", round(w, 2))

print("\nДиагнозы:")

for i, name in enumerate(class_names):
    print(i, "-", name)

with open(CLASS_FILE, "w", encoding="utf-8") as f:
    json.dump(class_names, f, ensure_ascii=False, indent=4)

AUTOTUNE = tf.data.AUTOTUNE

train_dataset = train_dataset.prefetch(AUTOTUNE)
validation_dataset = validation_dataset.prefetch(AUTOTUNE)

# ----------------------------
# АУГМЕНТАЦИЯ
# ----------------------------

data_augmentation = tf.keras.Sequential([
    tf.keras.layers.RandomFlip("horizontal"),
    tf.keras.layers.RandomRotation(0.15),
    tf.keras.layers.RandomZoom(0.10),
    tf.keras.layers.RandomContrast(0.10),
])

# ----------------------------
# EfficientNetB0
# ----------------------------

base_model = EfficientNetB0(
    include_top=False,
    weights="imagenet",
    input_shape=(224, 224, 3)
)

base_model.trainable = False

inputs = tf.keras.Input(shape=(224, 224, 3))

x = data_augmentation(inputs)

x = tf.keras.applications.efficientnet.preprocess_input(x)

x = base_model(x, training=False)

x = GlobalAveragePooling2D()(x)

x = Dropout(0.3)(x)

outputs = Dense(
    len(class_names),
    activation="softmax"
)(x)

model = Model(inputs, outputs)

# ----------------------------
# КОМПИЛЯЦИЯ МОДЕЛИ
# ----------------------------

model.compile(
    optimizer=tf.keras.optimizers.Adam(learning_rate=0.0001),
    loss="categorical_crossentropy",
    metrics=["accuracy"]
)

model.summary()

# ----------------------------
# CALLBACKS
# ----------------------------

checkpoint = ModelCheckpoint(
    MODEL_NAME,
    monitor="val_accuracy",
    save_best_only=True,
    verbose=1
)

early_stop = EarlyStopping(
    monitor="val_loss",
    patience=8,
    restore_best_weights=True,
    verbose=1
)

reduce_lr = ReduceLROnPlateau(
    monitor="val_loss",
    factor=0.2,
    patience=3,
    verbose=1,
    min_lr=1e-7
)

# ----------------------------
# ОБУЧЕНИЕ
# ----------------------------

history = model.fit(
    train_dataset,
    validation_data=validation_dataset,
    epochs=EPOCHS,
    class_weight=class_weights,
    callbacks=[
        checkpoint,
        early_stop,
        reduce_lr
    ]
)

# ----------------------------
# ДОПОЛНИТЕЛЬНОЕ ДООБУЧЕНИЕ
# ----------------------------

print("\nПереходим к Fine-Tuning...")

base_model.trainable = True

for layer in base_model.layers[:-20]:
    layer.trainable = False

model.compile(
    optimizer=tf.keras.optimizers.Adam(learning_rate=1e-5),
    loss="categorical_crossentropy",
    metrics=["accuracy"]
)

history_finetune = model.fit(
    train_dataset,
    validation_data=validation_dataset,
    epochs=20,
    class_weight=class_weights,
    callbacks=[
        checkpoint,
        early_stop,
        reduce_lr
    ]
)

# ----------------------------
# СОХРАНЕНИЕ
# ----------------------------

model.save(MODEL_NAME)

print("\n===================================")
print("Обучение завершено успешно!")
print("Модель сохранена:", MODEL_NAME)
print("Классы сохранены:", CLASS_FILE)
print("===================================")