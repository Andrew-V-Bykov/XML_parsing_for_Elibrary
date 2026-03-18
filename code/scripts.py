import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from rapidfuzz import process, fuzz
from lxml import etree as ET



TRESHHOD_SIMILARITY_PDF_AND_WORD: int = 60   


@dataclass
class SectionSpan:
    '''Раздел'''
    index: int
    title: str



@dataclass
class Author:
    '''Автор'''
    surname: str
    initials: str
    affiliations: dict[int, dict] = field(default_factory=dict)
    email: str | None = None



@dataclass
class ArticleSpan:
    '''Статья'''
    start: int
    end: int
    title: str
    title_norm: str

    start_page: int | None = None
    end_page: int | None = None
    art_type: str = "PRC"

    authors: list = field(default_factory=list)
    text: str = ""
    references: list[str] = field(default_factory=list)
    funding: str | None = None



def meaningful_runs(par):
    '''Отфильтровывает пустые параграфы'''
    return [r for r in par.runs if r.text and r.text.strip()]



def paragraph_font_size_pt(par):
    '''Определяет максимальный размер кегля в параграфе'''
    sizes = [r.font.size.pt for r in meaningful_runs(par) if r.font.size is not None]
    return max(sizes) if sizes else None



def is_section_header(par):
    '''Проверяет является ли параграф названием секции'''
    return par.text.strip().startswith('СЕКЦИЯ')



def is_article_title_candidate(par):
    '''Определяет возможные названия статей'''
    text = par.text.strip()
    if not text:
        return False
    if text.startswith('СЕКЦИЯ'):
        return False

    size = paragraph_font_size_pt(par)
    return size == 18



def normalize_title(s):
    '''Упрощает название статьи для сравнения заголовков после парсинга MS Word и PDF'''
    s = s.replace("\xa0", " ")     
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    s = s.replace("ё", "е")
    s = re.sub(r"-\s*", "", s)
    s = re.sub(r"[^a-z0-9а-я+]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s



def extract_title_from_page(page):
    '''Находит название тезисов на странице PDF'''
    chars = page.chars

    # группируем по размеру шрифта
    size_groups = defaultdict(list)
    for c in chars:
        size_groups[round(c["size"], 1)].append(c)
    if not size_groups:
        return None

    # самый крупный шрифт и есть заголовок
    max_size = max(size_groups.keys())
    title_chars = size_groups[max_size]

    # сортировка по позиции
    title_chars.sort(key=lambda c: (c["top"], c["x0"]))
    text = "".join(c["text"] for c in title_chars)
    text = re.sub(r'\s+', ' ', text).strip()

    # фильтры от мусора
    if len(text) < 20:
        return None
    if "секция" in text.lower():
        return None

    return text



def looks_like_article_title(text):
    '''Определяет потенциальный заголовок статьи'''
    if len(text) < 25:
        return False
    if "секция" in text:
        return False
    if text.count(" ") < 3:
        return False
    return True



def normalize_title_loose(s):
    '''Упрощает название статьи для сравнения заголовков после парсинга MS Word и PDF'''
    s = normalize_title(s)
    s = re.sub(r"\b[a-z]{2,}\b", lambda m: m.group(0), s)  # оставить слова
    s = re.sub(r"\b[a-z]\b", "", s)                        # убрать одиночные
    s = re.sub(r"\s+", " ", s)
    return s.strip()



def match_pdf_title(docx_title_norm, pdf_titles_loose, threshold=TRESHHOD_SIMILARITY_PDF_AND_WORD):
    '''Нестрогое сравнение названий статей, распаршенных из WORD и PDF'''
    query = normalize_title_loose(docx_title_norm)

    match = process.extractOne(query,
                               pdf_titles_loose.keys(),
                               scorer=fuzz.token_sort_ratio
                               )

    if match and match[1] >= threshold:
        return pdf_titles_loose[match[0]]

    return None



def assign_end_pages(elements, last_page):
    '''Определяет страницы конца статьи'''
    # берём только статьи с найденной start_page
    articles = [
        (i, e) for i, e in enumerate(elements)
        if isinstance(e, ArticleSpan) and e.start_page is not None
    ]

    for idx, (i, art) in enumerate(articles):
        # последняя статья
        if idx + 1 == len(articles):
            art.end_page = last_page
            continue

        next_i, next_art = articles[idx + 1]

        # проверяем, есть ли SectionSpan между статьями
        has_section_between = any(
            isinstance(e, SectionSpan)
            for e in elements[i + 1 : next_i]
        )

        if has_section_between:
            art.end_page = next_art.start_page - 2
        else:
            art.end_page = next_art.start_page - 1

        if art.end_page < art.start_page:
            raise ValueError(f"Некорректный диапазон страниц: {art.title}")



def is_empty(par):
    '''Проверка на пустую строку'''
    return not par.text.strip()



def is_authors_line(text):
    '''Определяет является ли параграф строкой с авторами'''
    return bool(re.search(r"[А-ЯA-Z][а-яa-z]+\s*[А-ЯA-Z]\.[А-ЯA-Z]\.\d?", text))



def is_email_line(text):
    '''Определяет является ли параграф e-mail-ом'''
    return "@" in text



def is_references_start(par):
    '''Определяет начало списка литературы'''
    text = par.text.strip()
    size = paragraph_font_size_pt(par)
    return text.startswith("[1]") or (size is not None and size < 14)



def is_funding_line(text, triggers=[]):
    '''Определяет является ли текст абзацем финансирования'''
    t = text.lower()
    return any(k in t for k in triggers)



def _clean_text(s: str) -> str:
    '''Убираем неразрывные пробелы и лишние пробелы'''
    s = s.replace("\xa0", " ")
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()



def parse_authors(line: str):
    """Парсим информацию об авторе

    Вход: строка авторов, например:
      "ФАМИЛИЯ(1) И(1).О(1).1, ФАМИЛИЯ(2) И(2).О(2).1,3, ФАМИЛИЯ(3) И(3).О(3).2, ..."
    Возвращает: список dict:
      [{'surname':ФАМИЛИЯ(1)','initials':'И(1).О(1).','affiliations':[1]},
      ...
      ]
    """
    line = _clean_text(line)
    if not line:
        return []

    # разбиваем по запятой+пробел — это гарантированно разделитель авторов в твоём корпусе
    tokens = re.split(r',\s+', line)

    authors = []
    any_aff = False

    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue

        # захватываем имя (включая инициалы) и ВСЕ цифры в конце (например "1,3" или "2")
        # захват делаем жадно для name и даём affs матчить цифры с запятыми в конце
        m = re.match(r'^(?P<name>.*?)(?P<affs>(?:\d+(?:\s*,\s*\d+)*)?)\s*$', tok)
        if not m:
            continue

        name_part = _clean_text(m.group('name') or "")
        affs_part = m.group('affs') or ""

        # преобразуем affs_part в список чисел (если есть)
        if affs_part.strip():
            affs = [int(x) for x in re.split(r'\s*,\s*', affs_part.strip()) if x.strip()]
            any_aff = True
        else:
            affs = []

        # Разделяем фамилию и инициалы. Обычно: "Фамилия И.О."
        # Берём первое слово за фамилию, всё остальное — за инициалы/имя
        parts = name_part.split()
        if len(parts) == 0:
            continue
        surname = parts[0]
        initials = " ".join(parts[1:]) if len(parts) > 1 else ""

        authors.append({
            "surname": surname,
            "initials": initials,
            "affiliations": affs
        })

    # если у никого нет пронумированной аффиляции (соотнесения цифр для организации) — назначаем всем [1]:
    if authors and not any_aff:
        for a in authors:
            a["affiliations"] = [1]

    return authors



def parse_affiliation(line, base_orgs=[], default_index=1):
    '''Парсим аффиляцию'''
    line = line.strip()

    # 1. Номер аффиляции
    m = re.match(r"^(?P<idx>\d+)\s*(?P<rest>.+)", line)
    if m:
        affil_index = int(m.group("idx"))
        rest = m.group("rest")
    else:
        affil_index = default_index
        rest = line

    parts = [p.strip() for p in rest.split(",") if p.strip()]

    # 2. Адрес
    if len(parts) >= 3 and re.match(r"\d{5,6}", parts[-3]):
        org_raw = ", ".join(parts[:-3])
        address = ", ".join(parts[-3:])
    elif len(parts) >= 2:
        org_raw = ", ".join(parts[:-2])
        address = ", ".join(parts[-2:])
    else:
        org_raw = rest
        address = ""

    # 3. Чистим скобочные аббревиатуры
    org_raw = re.sub(r"\s*\([^)]*\)", "", org_raw).strip(" ,")

    # 4. Делим orgName / otherInfo по словарю базовых организаций
    orgName = org_raw
    otherInfo = None

    for base in base_orgs:
        if base in org_raw:
            left, _, _ = org_raw.partition(base)
            orgName = base
            left = left.strip(" ,")
            if left:
                otherInfo = left
            break

    # 5. Собираем результат (без пустых полей)
    affil_dict = {
        "orgName": orgName,
        "address": address
    }

    if otherInfo is not None:
        affil_dict["otherInfo"] = otherInfo

    return affil_index, affil_dict



def strip_references(text):
    '''Вычищает из списка литературы номера ссылок'''
    # [1], [12], [1,2], [1–3], [1-3]
    text = re.sub(r"\[\s*\d+(?:\s*[-–,]\s*\d+)*\s*\]", "", text)
    # подчистим лишние пробелы
    text = re.sub(r"\s+", " ", text)
    return text.strip()



def parse_article(article: ArticleSpan, paragraphs):
    '''Парсит статью'''
    pars = paragraphs[article.start : article.end + 1]

    i = 0
    n = len(pars)

    # пропускаем заголовок (он первый в статье)
    while i < n and is_article_title_candidate(pars[i]):
        i += 1

    # пустые строки
    while i < n and is_empty(pars[i]):
        i += 1

    # авторы
    authors_line = pars[i].text.strip()
    i += 1

    # пустые строки
    while i < n and is_empty(pars[i]):
        i += 1

    # аффиляции
    affiliations = {}
    while not is_empty(pars[i]):
        result_affiliation = parse_affiliation(pars[i].text.strip().replace("\xa0", " "))
        affiliations[result_affiliation[0]] = result_affiliation[1]
        i += 1

    # пустые строки
    while i < n and is_empty(pars[i]):
        i += 1

    # email
    email = None
    if i < n and is_email_line(pars[i].text):
        email = pars[i].text.strip()
        i += 1

    # основной текст
    body_parts = []
    while i < n:
        par = pars[i]

        if is_references_start(par) or is_funding_line(par.text):
            break

        body_parts.append(par.text.strip().replace("\xa0", " ").replace("\n", ""))
        i += 1

    body_text = " ".join(body_parts)

    # funding
    funding = None
    if i < n and is_funding_line(pars[i].text):
        funding = pars[i].text.strip().replace('\xa0', ' ')
        i += 1

    # references
    references = []
    while i < n:
        if is_references_start(pars[i]):
            references.append(strip_references(pars[i].text.strip()))
        i += 1
    
    if len(references) > 0 and funding is not None:
        article.authors = parse_authors(authors_line)
        article.affiliations = affiliations
        article.email = email
        article.text = body_text
        article.funding = funding
        article.references = references

    elif len(references) > 0:
        article.authors = parse_authors(authors_line)
        article.affiliations = affiliations
        article.email = email
        article.text = body_text
        article.references = references

    elif funding is not None:
        article.authors = parse_authors(authors_line)
        article.affiliations = affiliations
        article.email = email
        article.text = body_text
        article.funding = funding
        
    else:
        article.authors = parse_authors(authors_line)
        article.affiliations = affiliations
        article.email = email
        article.text = body_text



def collect_affiliations(author_indices, affil_dict):
    '''Собирает наполнение тегов для аффиляций.'''
    orgs = []
    addresses = []
    other_infos = []

    for idx in author_indices:
        aff = affil_dict.get(idx)
        if not aff:
            continue

        orgs.append(aff["orgName"])
        addresses.append(aff.get("address", ""))

        if "otherInfo" in aff:
            other_infos.append(aff["otherInfo"])

    return {"orgName": "; ".join(orgs),
            "address": "; ".join(a for a in addresses if a),
            "otherInfo": "; ".join(other_infos) if other_infos else None
            }



def author_to_xml(author, num, affil_dict, email, is_first=False):
    '''Собирает XML тег с информацией об авторе.
    По дефолту приписывает указанный e-mail первому автору – это не всегда так, необходита потом перепроверить!!!'''
    el = ET.Element("author", {"num": f"{num:03d}"})

    info = ET.SubElement(el, "individInfo", {"lang": "RUS"})

    ET.SubElement(info, "surname").text = author['surname']
    ET.SubElement(info, "initials").text = author['initials']

    aff = collect_affiliations(author['affiliations'], affil_dict)

    ET.SubElement(info, "orgName").text = aff["orgName"]

    if is_first:
        ET.SubElement(info, "email").text = email

    ET.SubElement(info, "address").text = aff["address"]

    if aff["otherInfo"] is not None:
        ET.SubElement(info, "otherInfo").text = aff["otherInfo"]

    return el



def article_to_xml(article, current_section_title, file_name):
    '''Собирает XML тег для одной статьи.'''
    art = ET.Element("article")
    # print(article.email)

    # pages
    pages = ET.SubElement(art, "pages")
    if article.start_page == article.end_page:
        pages.text = str(article.start_page)
    else:
        pages.text = f"{article.start_page}-{article.end_page}"

    ET.SubElement(art, "artType").text = "PRC"

    # authors
    authors_el = ET.SubElement(art, "authors")
    for i, author in enumerate(article.authors, start=1):
        authors_el.append(author_to_xml(author,
                                        num=i,
                                        affil_dict=article.affiliations,
                                        email=article.email,
                                        is_first=(i == 1)
                                        )
                          )

    # titles
    titles = ET.SubElement(art, "artTitles")
    t = ET.SubElement(titles, "artTitle", {"lang": "RUS"})
    t.text = article.title

    # text
    text_el = ET.SubElement(art, "text", {"lang": "RUS"})
    text_el.text = article.text

    # references
    if article.references:
        refs = ET.SubElement(art, "references")
        for ref in article.references:
            ET.SubElement(refs, "reference").text = ref

    # files
    files = ET.SubElement(art, "files")
    f = ET.SubElement(files, "file", {"desc": "fullText"})
    f.text = file_name

    # funding
    if article.funding:
        fund = ET.SubElement(art, "artFunding")
        ET.SubElement(fund, "funding", {"lang": "RUS"}).text = article.funding

    return art