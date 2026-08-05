import tensorflow as tf

from tensorflow.keras import layers
from tensorflow.keras import models

img_size = 224
batch_size = 16

dataset = tf.keras.preprocessing.image_dataset_from_directory(
    "dataset",
    validation_split=0.2,
    subset="both",
    seed=123,
    image_size=(img_size, img_size),
    batch_size=batch_size
)

train_ds, val_ds = dataset

class_names = train_ds.class_names

print(class_names)
print(len(class_names))

model = models.Sequential([

    layers.Rescaling(1./255,
    input_shape=(img_size, img_size, 3)),

    layers.Conv2D(32, 3, activation='relu'),
    layers.MaxPooling2D(),

    layers.Conv2D(64, 3, activation='relu'),
    layers.MaxPooling2D(),

    layers.Conv2D(128, 3, activation='relu'),
    layers.MaxPooling2D(),

    layers.Flatten(),

    layers.Dense(128, activation='relu'),

    layers.Dense(len(class_names),
    activation='softmax')
])

model.compile(

    optimizer='adam',

    loss='sparse_categorical_crossentropy',

    metrics=['accuracy']
)

model.fit(
    train_ds,
    validation_data=val_ds,
    epochs=50
)

model.save("paithology_model.keras")

print("Модель обучена!")