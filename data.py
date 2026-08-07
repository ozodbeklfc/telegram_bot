# ======================================================================
# Справочники для select-полей формы "Добавление ТТ".
# Это перенесённые 1-в-1 значения из твоего Mini App (dropdownData).
# Если нужно поменять список — редактируй прямо здесь.
# ======================================================================

REGIONS = ["FV", "SOUTH-WEST", "TASHKENT", "TASH-OBL"]

OBLAST = {
    "FV": ["FARGONA VILOYATI", "NAMANGAN VILOYATI", "ANDIJON VILOYATI"],
    "SOUTH-WEST": [
        "BUXORO VILOYATI", "JIZZAX VILOYATI", "NAVOIY VILOYATI",
        "QASHQADARYO VILOYATI", "QORAQALPOQISTON", "SAMARQAND VILOYATI",
        "SURXANDARYO VILOYATI", "XORAZM VILOYATI",
    ],
    "TASHKENT": ["TASHKENT"],
    "TASH-OBL": ["TOSHKENT VILOYATI", "SIRDARYO VILOYATI"],
}

_OKRUG_RAYON_COMMON = {
    "TASHKENT": ["BEKTEMIR", "CHILONZOR", "YASHNOBOD", "MIROBOD", "MIRZO ULUGBEK", "SERGELI",
                 "SHAYXONTOXUR", "OLMAZOR", "UCHTEPA", "YAKKASAROY", "YUNUSOBOD", "YANGIHAYOT",
                 "ASKIYA BOZOR", "ECO BOZOR", "FOOD CITY BOZOR", "GVARDEYSKIY BOZOR",
                 "KADISHEVA BOZOR", "MIROBOD BOZOR", "OLOY BOZOR", "QATORTOL BOZOR",
                 "QOYLIQ BOZOR", "SAMPI BOZOR/UNIVERSAM BOZOR", "URIKZOR BOZOR",
                 "VODIY BOZOR", "IPPODROM BOZOR"],
    "TOSHKENT VILOYATI": ["BEKOBOD", "BOKA", "BOSTONLIQ", "ZANGIOTA", "OQQORGON", "OHANGARON",
                           "PARKENT", "PISKENT", "CHIRCHIQ", "YANGIYOL", "CHINOZ", "QIBRAY"],
    "SIRDARYO VILOYATI": ["GULISTON", "OQOLTIN", "BOYOVUT", "XOVOS", "MIRZAOBOD", "SARDOBA",
                           "SAYXUNOBOD", "SIRDARYO"],
    "JIZZAX VILOYATI": ["JIZZAKH", "ARNASOY", "BAXMAL", "DOSTLIK", "FORISH", "GALLAOROL",
                         "SHAROF RASHIDOV", "MIRZACHOL", "PAXTAKOR", "YANGIOBOD", "ZOMIN",
                         "ZAFAROBOD", "ZARBDOR"],
    "SAMARQAND VILOYATI": ["SAMARQAND", "BULUNGUR", "ISHTIXON", "JOMBOY", "KATTAQORGON",
                            "QOSHRABOT", "NARPAY", "NUROBOD", "OQDARYO", "PAXTACHI", "PAYARIQ",
                            "PASTDARGOM", "TOYLOQ", "URGUT"],
    "NAVOIY VILOYATI": ["NAVOIY", "KONIMEX", "QIZILTEPA", "XATIRCHI", "NAVBAHOR", "KARMANA",
                         "NUROTA", "ZARAFSHAN", "TOMDI", "UCHQUDUQ"],
    "BUXORO VILOYATI": ["BUXORO", "OLOT", "GIJDUVON", "JONDOR", "KOGON", "QORAKOL",
                         "QOROVULBOZOR", "PESHKU", "ROMITAN", "SHOFIRKON", "VOBKENT"],
    "QASHQADARYO VILOYATI": ["QARSHI", "DEHQONOBOD", "GUZOR", "KOSON", "KASBI", "MIRISHKOR",
                              "MUBORAK", "NISHON", "SHAHRISABZ", "CHIROQCHI", "QAMASHI",
                              "KITOB", "YAKKABOG", "KOKDALA"],
    "SURXANDARYO VILOYATI": ["TERMIZ", "ANGOR", "BANDIXON", "JARQORGON", "QIZIRIQ", "MUZRABOT",
                              "SHEROBOD", "DENOV", "BOYSUN", "QUMQORGON", "OLTINSOY",
                              "SARIOSIYO", "SHORCHI", "UZUN"],
    "XORAZM VILOYATI": ["URGANCH", "XIVA", "BOGOT", "GURLAN", "XONQA", "HAZORASP", "QOSHKOPIR",
                         "SHOVOT", "YANGIARIQ", "YANGIBOZOR", "TUPROQQALA"],
    "QORAQALPOQISTON": ["NUKUS", "AMUDARYO", "BERUNIY", "CHIMBOY", "ELLIKQALA", "KEGEYLI",
                         "MOYNOQ", "QANLIKOL", "QONGIROT", "QORAOZAK", "SHUMANAY",
                         "TAXTAKOPIR", "TORTKOL", "XOJAYLI", "TAXIATOSH", "BOZATOV"],
    "NAMANGAN VILOYATI": ["NAMANGAN", "CHORTOQ", "CHUST", "KOSONSOY", "MINGBULOQ", "NORIN",
                           "POP", "TORAQORGON", "UCHQORGON", "UYCHI", "YANGIQORGON"],
    "FARGONA VILOYATI": ["FARGONA", "OLTIARIQ", "QOSHTEPA", "QUVA", "SOX", "TOSHLOQ",
                          "YOZYOVON", "QOQAND", "BAGDOD", "BESHARIQ", "BUVAYDA", "DANGARA",
                          "FURQAT", "RISHTON", "UCHKOPRIK", "OZBEKISTON"],
    "ANDIJON VILOYATI": ["ANDIJON", "ASAKA", "BALIQCHI", "BOSTON", "BULOQBOSHI", "IZBOSKAN",
                          "JALAQUDUQ", "XOJAOBOD", "QORGONTEPA", "MARHAMAT", "OLTINKOL",
                          "PAXTAOBOD", "SHAHRIXON", "ULUGNOR"],
}

# Округ и район сейчас используют одинаковые списки (как было в исходном скрипте).
# Если у тебя это разные справочники — просто отредактируй один из словарей ниже отдельно.
OKRUG = {k: list(v) for k, v in _OKRUG_RAYON_COMMON.items()}
RAYON = {k: list(v) for k, v in _OKRUG_RAYON_COMMON.items()}

FORMAT = ["Chain", "Drogery", "Hotels", "OP.Markets", "Others", "Perfumery", "Pharmacy",
          "Super M.", "Superettes", "Web Sales"]

CHANNEL = ["B.SALOONS", "HORECA", "MOD.TRADE", "TRAD TRADE", "WHOLESALE"]

TYPE = ["FOOD", "FOOD-HPC", "HPC"]

CATEGORY = ["A", "B", "C", "D"]

DELIVERY_CODE = {
    "TASHKENT": ["D2-TOP1", "D3-TOP2", "D4-YNSBD-1", "D5-SHAYXTR", "D6-OLMAZR", "D7-CLNZR1",
                 "D8-YNSBD-2", "D9-M.ULU1", "D10-UCHTEP", "D11-MIR.YK", "D12-CLNZR2",
                 "D13-YASHNB", "D14-SRGILI", "D15-M.ULU2", "D16-CHRQ", "D17-CHRQ-1",
                 "D18-CHRQ-2", "D19-OSDO-1", "D20-OSDO-2", "D21-OSDO-3", "D22-YANG-1",
                 "D23-YANG-2", "D24-BEKBOD", "D25-ANG.OX", "D26-OLMALQ", "K1-KA", "K3-KA", "K5-KA"],
    "TOSHKENT VILOYATI": ["D2-TOP1", "D3-TOP2", "D4-YNSBD-1", "D5-SHAYXTR", "D6-OLMAZR",
                           "D7-CLNZR1", "D8-YNSBD-2", "D9-M.ULU1", "D10-UCHTEP", "D11-MIR.YK",
                           "D12-CLNZR2", "D13-YASHNB", "D14-SRGILI", "D15-M.ULU2", "D16-CHRQ",
                           "D17-CHRQ-1", "D18-CHRQ-2", "D19-OSDO-1", "D20-OSDO-2", "D21-OSDO-3",
                           "D22-YANG-1", "D23-YANG-2", "D24-BEKBOD", "D25-ANG.OX", "D26-OLMALQ",
                           "K1-KA", "K3-KA", "K5-KA"],
    "SAMARQAND VILOYATI": ["SAM-D1", "SAM-D3", "SAM-D4", "SAM-D5", "SAM-D6", "SAM-D8", "SAM-D7"],
    "BUXORO VILOYATI": ["BUX-D3", "BUX-D6", "BUX-D4", "BUX-D2", "BUX-D5", "BUX-D7", "BUX-D1"],
    "NAVOIY VILOYATI": ["NAV-D2", "NAV-D1", "NAV-D3", "NAV-D4"],
    "JIZZAX VILOYATI": ["JIZ-D2", "JIZ-D3", "JIZ-D1"],
    "SURXANDARYO VILOYATI": ["TER-D1", "TER-DJ", "TER-D2"],
    "QASHQADARYO VILOYATI": ["QAR-D3", "QAR-D1", "QAR-D2", "QAR-D4", "SHAH-D2", "SHAH-D1"],
    "XORAZM VILOYATI": ["XOR-D4", "XOR-D2", "XOR-D5", "XOR-D3", "XOR-D1", "XOR-D6"],
    "QORAQALPOQISTON": ["NUK-D3", "NUK-D2", "NUK-D1"],
    "FARGONA VILOYATI": ["FAR-D1", "FAR-D2", "FAR-D3", "FAR-D4", "FAR-D5", "QOQ-D1", "QOQ-D2",
                          "QOQ-D3", "QOQ-D4", "QOQ-D5"],
    "ANDIJON VILOYATI": ["AND-D1", "AND-D2", "AND-D3", "AND-D4", "AND-D5"],
    "NAMANGAN VILOYATI": ["NAM-D1", "NAM-D2", "NAM-D3", "NAM-D4", "NAM-D5"],
    "DEFAULT": ["DLV-101", "DLV-102", "DLV-103"],
}

DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
