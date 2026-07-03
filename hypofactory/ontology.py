"""Доменная онтология обогащения медно-никелевых руд.

Затравочный слой графа знаний: минералы, операции, оборудование, параметры,
механизмы и их причинно-следственные связи. Составлен по материалам кейса
(регламенты, типовые списки оборудования, «Как читать отчет института по
хвостам», примеры мозговых штурмов) и классической литературе по флотации.

Каждый «рычаг» (lever) — это управляемое воздействие, к которому диагностика
привязывает сигналы потерь. Рычаги несут атрибуты для прозрачного
ранжирования: capex-класс, сроки, тип проверки.
"""
from __future__ import annotations

# ---------------------------------------------------------------- узлы графа
# type: mineral | operation | equipment | parameter | mechanism | kpi | reagent
SEED_NODES: list[dict] = [
    # Минеральные формы потерь (из отчётов института)
    {"id": "pnt_open", "label": "Раскрытый Pnt/Cp", "type": "mineral",
     "desc": "Свободные зёрна пентландита/халькопирита — уже раскрыты, но не извлечены флотацией"},
    {"id": "pnt_locked", "label": "Закрытый Pnt/Cp", "type": "mineral",
     "desc": "Сростки пентландита/халькопирита с породой — требуют доизмельчения"},
    {"id": "po_admix", "label": "Примесь Ni в пирротине", "type": "mineral",
     "desc": "Изоморфный никель в решётке пирротина — флотацией не отделяется"},
    {"id": "silicate", "label": "Силикатная форма / валлериит", "type": "mineral",
     "desc": "Металл в силикатах и валлериите — текущей технологией не извлекается"},
    {"id": "millerite", "label": "Миллерит", "type": "mineral",
     "desc": "Сульфид никеля NiS, потенциально извлекаем"},
    {"id": "pyrite", "label": "Пирит / другие сульфиды Cu", "type": "mineral"},
    {"id": "pyrrhotite", "label": "Пирротин", "type": "mineral",
     "desc": "Магнитный сульфид железа, носитель примесного Ni"},

    # Операции
    {"id": "grinding", "label": "Измельчение", "type": "operation"},
    {"id": "regrinding", "label": "Доизмельчение", "type": "operation"},
    {"id": "classification", "label": "Классификация", "type": "operation"},
    {"id": "flotation_main", "label": "Основная флотация", "type": "operation"},
    {"id": "flotation_scav", "label": "Контрольная флотация", "type": "operation"},
    {"id": "flotation_clean", "label": "Перечистная флотация", "type": "operation"},
    {"id": "magnetic_sep", "label": "Магнитная сепарация", "type": "operation"},
    {"id": "gravity_sep", "label": "Гравитационное обогащение", "type": "operation"},
    {"id": "thickening", "label": "Сгущение / контактирование", "type": "operation"},

    # Оборудование (из типовых списков кейса)
    {"id": "ball_mill", "label": "Шаровая мельница", "type": "equipment"},
    {"id": "hydrocyclone", "label": "Гидроциклон", "type": "equipment"},
    {"id": "spiral_classifier", "label": "Спиральный классификатор", "type": "equipment"},
    {"id": "fine_screen", "label": "Грохот тонкого грохочения", "type": "equipment"},
    {"id": "flot_machine", "label": "Флотомашина", "type": "equipment"},
    {"id": "contact_tank", "label": "Контактный чан", "type": "equipment"},
    {"id": "cone_crusher", "label": "Конусная дробилка", "type": "equipment"},

    # Управляемые параметры
    {"id": "grind_fineness", "label": "Тонина помола (% -71 мкм)", "type": "parameter"},
    {"id": "liner_geometry", "label": "Геометрия футеровки мельниц", "type": "parameter"},
    {"id": "ball_charge", "label": "Шаровая загрузка (диаметр/класс шаров)", "type": "parameter"},
    {"id": "cyclone_spigot", "label": "Диаметр песковой насадки гидроциклона", "type": "parameter"},
    {"id": "pulp_density", "label": "Плотность пульпы", "type": "parameter"},
    {"id": "flot_time", "label": "Время флотации", "type": "parameter"},
    {"id": "reagent_regime", "label": "Реагентный режим (ксантогенат, аэрофлот, вспениватель)", "type": "parameter"},
    {"id": "air_flow", "label": "Расход воздуха / аэрация", "type": "parameter"},
    {"id": "circulating_load", "label": "Циркулирующая нагрузка", "type": "parameter"},
    {"id": "water_feed", "label": "Подача воды в мельницы", "type": "parameter"},

    # Механизмы
    {"id": "mech_liberation", "label": "Раскрытие сростков", "type": "mechanism",
     "desc": "Доизмельчение вскрывает сульфидные зёрна из сростков с породой"},
    {"id": "mech_overgrind", "label": "Переизмельчение / ошламование", "type": "mechanism",
     "desc": "Тонкие шламы (<10 мкм) плохо флотируются: малая инерция частиц, окисление поверхности"},
    {"id": "mech_kinetics", "label": "Кинетика флотации", "type": "mechanism",
     "desc": "Недостаточное время пребывания → недоизвлечение медленно флотируемых зёрен"},
    {"id": "mech_selectivity", "label": "Селективность разделения", "type": "mechanism"},
    {"id": "mech_bypass", "label": "Проскок крупных частиц", "type": "mechanism",
     "desc": "Неэффективная классификация пропускает крупные сростки в флотацию"},
    {"id": "mech_surface", "label": "Гидрофобизация поверхности", "type": "mechanism",
     "desc": "Собиратель адсорбируется на сульфидной поверхности, повышая флотируемость"},
    {"id": "mech_magnetic", "label": "Магнитные свойства пирротина", "type": "mechanism"},

    # KPI
    {"id": "kpi_ni_loss", "label": "Потери Ni с хвостами", "type": "kpi"},
    {"id": "kpi_cu_loss", "label": "Потери Cu с хвостами", "type": "kpi"},
    {"id": "kpi_recovery", "label": "Сквозное извлечение металлов", "type": "kpi"},
]

# ---------------------------------------------------------------- рёбра графа
# (src, relation, dst, desc)
SEED_EDGES: list[tuple[str, str, str, str]] = [
    # минералы → механизмы/KPI
    ("pnt_locked", "требует", "mech_liberation", "закрытые сростки извлекаются только после раскрытия"),
    ("pnt_open", "теряется_из_за", "mech_kinetics", "раскрытые зёрна теряются при нехватке времени/реагентов"),
    ("pnt_open", "теряется_из_за", "mech_overgrind", "раскрытые зёрна в классе -10 мкм ошламованы"),
    ("po_admix", "связан_с", "mech_magnetic", "пирротин выделяется магнитной сепарацией"),
    ("millerite", "извлекается_через", "mech_surface", "миллерит флотируется при усиленном собирателе"),
    ("pnt_locked", "влияет_на", "kpi_ni_loss", ""),
    ("pnt_open", "влияет_на", "kpi_ni_loss", ""),
    ("po_admix", "влияет_на", "kpi_ni_loss", ""),
    ("silicate", "влияет_на", "kpi_ni_loss", "неизвлекаемая форма — потолок извлечения"),
    ("pnt_open", "влияет_на", "kpi_cu_loss", ""),
    ("pnt_locked", "влияет_на", "kpi_cu_loss", ""),

    # операции/оборудование → механизмы
    ("regrinding", "усиливает", "mech_liberation", "отдельный цикл доизмельчения песков/промпродукта"),
    ("grinding", "усиливает", "mech_liberation", ""),
    ("grinding", "может_вызывать", "mech_overgrind", "избыточное измельчение легко шламуемых сульфидов"),
    ("classification", "предотвращает", "mech_bypass", "точная классификация не пропускает крупные классы"),
    ("classification", "предотвращает", "mech_overgrind", "своевременный вывод готового класса из цикла"),
    ("fine_screen", "улучшает", "classification", "грохочение разделяет по размеру, а не по плотности"),
    ("hydrocyclone", "реализует", "classification", ""),
    ("spiral_classifier", "реализует", "classification", ""),
    ("magnetic_sep", "выделяет", "pyrrhotite", "магнитная фракция с примесным Ni"),
    ("gravity_sep", "извлекает", "pnt_open", "плотные сульфиды из крупных классов"),
    ("flotation_scav", "извлекает", "pnt_open", "медленно флотируемые зёрна"),
    ("contact_tank", "усиливает", "mech_surface", "время контакта пульпы с реагентами"),

    # параметры → операции/механизмы
    ("liner_geometry", "настраивает", "grinding", "профиль футеровки меняет траекторию шаров и энергию удара"),
    ("ball_charge", "настраивает", "grinding", "диаметр шаров определяет баланс удар/истирание"),
    ("grind_fineness", "управляет", "mech_liberation", ""),
    ("cyclone_spigot", "настраивает", "hydrocyclone", "диаметр насадки смещает границу разделения"),
    ("pulp_density", "настраивает", "flotation_main", "плотность влияет на время пребывания и вязкость"),
    ("pulp_density", "настраивает", "hydrocyclone", ""),
    ("flot_time", "управляет", "mech_kinetics", ""),
    ("reagent_regime", "управляет", "mech_surface", ""),
    ("air_flow", "управляет", "mech_kinetics", ""),
    ("circulating_load", "настраивает", "classification", ""),
    ("water_feed", "настраивает", "grinding", "разжижение пульпы в мельнице"),
    ("cone_crusher", "влияет_на", "grind_fineness", "гранулометрия питания мельниц"),

    # операции → KPI
    ("regrinding", "снижает", "kpi_ni_loss", ""),
    ("flotation_scav", "снижает", "kpi_ni_loss", ""),
    ("magnetic_sep", "снижает", "kpi_ni_loss", ""),
    ("flotation_main", "определяет", "kpi_recovery", ""),
]

# ------------------------------------------------------------------- рычаги
# Управляемые воздействия, к которым диагностика привязывает сигналы.
# capex: 0 = настройка (дни), 1 = замена узла (месяцы), 2 = новый передел (год+)
LEVERS: dict[str, dict] = {
    "regrind_cycle": {
        "label": "Выделенный цикл доизмельчения проблемного класса",
        "nodes": ["regrinding", "ball_mill", "mech_liberation"],
        "capex": 1, "test": "лабораторное доизмельчение пробы хвостов + флотация",
    },
    "liner_geometry": {
        "label": "Изменение геометрии футеровки мельниц",
        "nodes": ["liner_geometry", "grinding", "ball_mill"],
        "capex": 1, "test": "DEM-моделирование + опытная кампания на одной мельнице",
    },
    "ball_charge": {
        "label": "Оптимизация шаровой загрузки (диаметр/класс шаров)",
        "nodes": ["ball_charge", "grinding"],
        "capex": 0, "test": "опытная загрузка на одной мельнице, контроль гранулометрии",
    },
    "cyclone_tuning": {
        "label": "Настройка гидроциклонов (насадки, давление)",
        "nodes": ["cyclone_spigot", "hydrocyclone", "classification"],
        "capex": 0, "test": "замена насадок на одной батарее, опробование разгрузок",
    },
    "classifier_replace": {
        "label": "Замена классификаторов на гидроциклоны / модернизация",
        "nodes": ["spiral_classifier", "hydrocyclone", "classification"],
        "capex": 1, "test": "пилотная батарея гидроциклонов на одной секции",
    },
    "fine_screening": {
        "label": "Тонкое грохочение в цикле измельчения",
        "nodes": ["fine_screen", "classification", "mech_bypass"],
        "capex": 1, "test": "пилотный грохот на части потока",
    },
    "flot_time_redistribution": {
        "label": "Перераспределение фронта флотации / время операций",
        "nodes": ["flot_time", "flotation_scav", "mech_kinetics"],
        "capex": 0, "test": "кинетические опыты + перебалансировка камер",
    },
    "reagent_optimization": {
        "label": "Оптимизация реагентного режима",
        "nodes": ["reagent_regime", "mech_surface", "contact_tank"],
        "capex": 0, "test": "лабораторные флотационные опыты с вариацией дозировок",
    },
    "pulp_density_tuning": {
        "label": "Повышение/оптимизация плотности пульпы",
        "nodes": ["pulp_density", "flotation_main"],
        "capex": 0, "test": "ступенчатое изменение плотности с контролем извлечения",
    },
    "contact_tanks": {
        "label": "Промежуточные контактные чаны перед флотацией",
        "nodes": ["contact_tank", "mech_surface", "flotation_scav"],
        "capex": 1, "test": "лабораторная оценка времени агитации",
    },
    "magnetic_separation": {
        "label": "Магнитная сепарация пирротина с доизмельчением магнитной фракции",
        "nodes": ["magnetic_sep", "pyrrhotite", "po_admix", "regrinding"],
        "capex": 1, "test": "магнитный анализ проб + флотация магнитной фракции",
    },
    "gravity_circuit": {
        "label": "Гравитационное доизвлечение из крупных классов",
        "nodes": ["gravity_sep", "pnt_open"],
        "capex": 1, "test": "обогащение на концентрационном столе / отсадка пробы",
    },
    "scavenger_boost": {
        "label": "Усиление контрольной флотации (камеры, аэрация)",
        "nodes": ["flotation_scav", "air_flow", "mech_kinetics"],
        "capex": 0, "test": "кинетика контрольной флотации, подбор аэрации",
    },
    "feed_granulometry": {
        "label": "Контроль гранулометрии питания мельниц (дробилки)",
        "nodes": ["cone_crusher", "grind_fineness", "grinding"],
        "capex": 0, "test": "автоматический контроль зазора дробилок + ситовки питания",
    },
    "tailings_reflotation": {
        "label": "Классификация хвостов и возврат фракции в голову процесса",
        "nodes": ["classification", "flotation_scav", "kpi_recovery"],
        "capex": 1, "test": "флотация выделенной фракции текущих хвостов",
    },
    "hydromet_leach": {
        "label": "Гидрометаллургическое доизвлечение неизвлекаемых форм",
        "nodes": ["silicate", "po_admix", "kpi_ni_loss"],
        "capex": 2, "test": "лабораторное выщелачивание проб хвостов",
    },
}
