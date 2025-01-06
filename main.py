import os
import zipfile
import re
import shutil
import subprocess

def sanitize_filename(filename):
    sanitized = re.sub(r'[^a-zA-Zа-яА-Я0-9]', '_', filename)
    sanitized = re.sub(r'_+', '_', sanitized).strip('_')
    return sanitized

def generate_image_from_typst(typst_file, output_image_path):
    output_image_path_with_page = output_image_path.replace(".png", "-{p}.png")
    try:
        subprocess.run(["typst", "compile", typst_file, output_image_path_with_page], check=True)
    except FileNotFoundError:
        print("Typst не установлен или недоступен. Убедитесь, что Typst установлен и доступен в PATH.")
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при генерации изображения из {typst_file}: {e}")

def split_typst_file(input_file, output_dir, images_dir, added_text_file, extract_to):
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)

    with open(added_text_file, 'r', encoding='utf-8') as added_file:
        added_text = added_file.read()

    with open(input_file, 'r', encoding='utf-8') as file:
        content = file.read()

    sections = content.split("\n== ")

    for i, section in enumerate(sections):
        if i == 0:
            section_title = "intro"
            section_content = section
            continue
        else:
            section_split = section.split("\n", 1)
            section_title = section_split[0].strip()
            section_content = section_split[1] if len(section_split) > 1 else ""

        section_content = re.sub(r'\n=+.*$', '', section_content.strip())
        section_content = re.sub(r'#link\(label\([^)]+\)\)\[.*?\]', '', section_content)

        sanitized_title = sanitize_filename(section_title)
        filename = f"{i:02d}_{sanitized_title}.typst"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as output_file:
            output_file.write(f"\n{added_text}\n== {section_title}\n{section_content}\n")

        image_filename = f"{i:02d}_{sanitized_title}.png"
        image_filepath = os.path.join(images_dir, image_filename)
        generate_image_from_typst(filepath, image_filepath)

def extract_archive(archive_path, extract_to):
    os.makedirs(extract_to, exist_ok=True)
    with zipfile.ZipFile(archive_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)

    typst_file = os.path.join(extract_to, "main.typ")
    if not os.path.isfile(typst_file):
        typst_file = None

    return typst_file

def copy_files_to_output_directory(extract_to, output_directory):
    os.makedirs(output_directory, exist_ok=True)
    for root, _, files in os.walk(extract_to):
        for file in files:
            source_path = os.path.join(root, file)
            destination_path = os.path.join(output_directory, file)
            shutil.copy(source_path, destination_path)
            print(f"Скопирован: {source_path} -> {destination_path}")

def remove_comments_from_file(file_path):
    """
    Удаляет однострочные и многострочные комментарии из файла.

    :param file_path: Путь к файлу, который нужно обработать
    """
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()

    # Удаление комментариев (однострочных и многострочных)
    content_without_comments = re.sub(r'//.*|/\*[\s\S]*?\*/', '', content)

    # Сохраняем результат обратно в файл
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(content_without_comments)
    print(f"Комментарии успешно удалены из файла: {file_path}")

# Пример использования
archive_path = "Calc_S3_Exam.zip"
extract_to = "extracted_content"
output_directory = "output_sections"
images_directory = "output_images"
added_text_path = "added.txt"

typst_file = extract_archive(archive_path, extract_to)

if typst_file:
    # Удаляем комментарии из файла main.typ
    remove_comments_from_file(typst_file)

    # Копируем файлы из extracted_content в output_sections
    copy_files_to_output_directory(extract_to, output_directory)

    # Разделяем файл .typst и генерируем изображения
    split_typst_file(typst_file, output_directory, images_directory, added_text_path, extract_to)
else:
    print("Файл main.typ не найден в архиве.")
