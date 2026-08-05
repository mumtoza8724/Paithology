from PIL import Image
import os

bad_files = []
checked = 0

for root, dirs, files in os.walk("dataset"):
    for file in files:
        if file.lower().endswith((".png", ".jpg", ".jpeg")):
            path = os.path.join(root, file)
            checked += 1

            if checked % 500 == 0:
                print(f"Проверено: {checked} файлов...")

            try:
                with Image.open(path) as img:
                    img.load()

            except Exception as e:
                print(f"\n❌ Поврежден: {path}")
                print(f"Ошибка: {e}")
                bad_files.append(path)

print("\n==============================")
print(f"Всего проверено: {checked}")
print(f"Поврежденных файлов: {len(bad_files)}")
print("==============================")

if bad_files:
    print("\nСписок поврежденных файлов:\n")

    with open("bad_files.txt", "w", encoding="utf-8") as f:
        for file in bad_files:
            print(file)
            f.write(file + "\n")

    print("\nСписок также сохранен в bad_files.txt")
else:
    print("Все изображения исправны.")