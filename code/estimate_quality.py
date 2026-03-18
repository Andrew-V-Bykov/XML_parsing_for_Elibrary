# Код для оценки качества разметки
from lxml import etree
from collections import Counter

auto_xml = "AUTI.xml"                  # Название XML, полученного автоматическим парсингом
true_xml = "conf_unicode.xml"          # Название XML, полученного после ручной проверки

def extract_all_tag_texts(tree):
    '''Извлекаем все теги'''
    result = []

    for el in tree.iter():
        text = (el.text or "").replace("\xa0", " ").strip()
        if text:
            result.append((el.tag, text))

    return result

def compute_pr(auto_vals, gold_vals):
    '''Рассчитывет метрики'''
    auto_c = Counter(auto_vals)
    gold_c = Counter(gold_vals)

    tp = sum((auto_c & gold_c).values())  # пересечение
    fp = sum((auto_c - gold_c).values())
    fn = sum((gold_c - auto_c).values())

    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0

    return precision, recall, tp, fp, fn

auto_tree = etree.parse(auto_xml)
gold_tree = etree.parse(true_xml)

auto_vals = extract_all_tag_texts(auto_tree)
gold_vals = extract_all_tag_texts(gold_tree)

p, r, tp, fp, fn = compute_pr(auto_vals, gold_vals)

print(f"ALL TAGS: Precision = {p:.4f}, Recall = {r:.4f}")
print(f"TP={tp}, FP={fp}, FN={fn}")