from PIL import Image
import os

count = 0

for root, dirs, files in os.walk("dataset"):
    for file in files:
        if file.lower().endswith(".png"):
            path = os.path.join(root, file)

            try:
                img = Image.open(path).convert("RGB")
                img.save(path, "PNG")

                count += 1

                if count % 500 == 0:
                    print(f"Исправлено {count} файлов")

            except Exception as e:
                print("Не удалось обработать:", path)
                print(e)

print("Готово.")