"""
Справочники для формы добавления торговой точки.

Иерархия: РЕГИОН -> ОБЛАСТЬ -> ОКРУГ -> РАЙОН.
Списки построены по файлу "Лист_Microsoft_Excel.xlsx" и административному
делению Узбекистана. В файле три столбца шли без связи между собой, поэтому
районы разложены по округам по фактической принадлежности:
например, ANGREN SHAHAR и OLMALIQ SHAHAR относятся к округу OHANGARON,
а QOQON SHAHAR и MARGILON TUMANI — к округам QOQAND и FARGONA.

Правки списков делаются прямо здесь.
"""

REGIONS = ['FV', 'SOUTH-WEST', 'TASHKENT', 'TASH-OBL']

# Регион -> области
OBLAST = {
    "FV": [
        "FARGONA VILOYATI", "NAMANGAN VILOYATI", "ANDIJON VILOYATI", "KOKAND VILOYATI"
    ],
    "SOUTH-WEST": [
        "BUXORO VILOYATI", "JIZZAX VILOYATI", "NAVOIY VILOYATI", "QASHQADARYO VILOYATI",
        "QORAQALPOQISTON", "SAMARQAND VILOYATI", "SURXANDARYO VILOYATI", "XORAZM VILOYATI"
    ],
    "TASHKENT": [
        "TASHKENT"
    ],
    "TASH-OBL": [
        "TOSHKENT VILOYATI", "SIRDARYO VILOYATI"
    ],
}

# Область -> округа
OKRUG = {
    "TASHKENT": [
        "TASHKENT"
    ],
    "TOSHKENT VILOYATI": [
        "BEKOBOD", "BOKA", "BOSTONLIQ", "ZANGIOTA", "OQQORGON", "OHANGARON", "PARKENT", "PISKENT",
        "CHIRCHIQ", "YANGIYOL", "CHINOZ", "QIBRAY"
    ],
    "SIRDARYO VILOYATI": [
        "GULISTON", "OQOLTIN", "BOYOVUT", "XOVOS", "MIRZAOBOD", "SARDOBA", "SAYXUNOBOD",
        "SIRDARYO"
    ],
    "JIZZAX VILOYATI": [
        "JIZZAKH", "ARNASOY", "BAXMAL", "DOSTLIK", "FORISH", "GALLAOROL", "SHAROF RASHIDOV",
        "MIRZACHOL", "PAXTAKOR", "YANGIOBOD", "ZOMIN", "ZAFAROBOD", "ZARBDOR"
    ],
    "SAMARQAND VILOYATI": [
        "SAMARQAND", "BULUNGUR", "ISHTIXON", "JOMBOY", "KATTAQORGON", "QOSHRABOT", "NARPAY",
        "NUROBOD", "OQDARYO", "PAXTACHI", "PAYARIQ", "PASTDARGOM", "TOYLOQ", "URGUT"
    ],
    "NAVOIY VILOYATI": [
        "NAVOIY", "KONIMEX", "QIZILTEPA", "XATIRCHI", "NAVBAHOR", "KARMANA", "NUROTA", "ZARAFSHAN",
        "TOMDI", "UCHQUDUQ"
    ],
    "BUXORO VILOYATI": [
        "BUXORO", "OLOT", "GIJDUVON", "JONDOR", "KOGON", "QORAKOL", "QOROVULBOZOR", "PESHKU",
        "ROMITAN", "SHOFIRKON", "VOBKENT"
    ],
    "QASHQADARYO VILOYATI": [
        "QARSHI", "DEHQONOBOD", "GUZOR", "KOSON", "KASBI", "MIRISHKOR", "MUBORAK", "NISHON",
        "SHAHRISABZ", "CHIROQCHI", "QAMASHI", "KITOB", "YAKKABOG", "KOKDALA"
    ],
    "SURXANDARYO VILOYATI": [
        "TERMIZ", "ANGOR", "BANDIXON", "JARQORGON", "QIZIRIQ", "MUZRABOT", "SHEROBOD", "DENOV",
        "BOYSUN", "QUMQORGON", "OLTINSOY", "SARIOSIYO", "SHORCHI", "UZUN"
    ],
    "XORAZM VILOYATI": [
        "URGANCH", "XIVA", "BOGOT", "GURLAN", "XONQA", "HAZORASP", "QOSHKOPIR", "SHOVOT",
        "YANGIARIQ", "YANGIBOZOR", "TUPROQQALA"
    ],
    "QORAQALPOQISTON": [
        "NUKUS", "AMUDARYO", "BERUNIY", "CHIMBOY", "ELLIKQALA", "KEGEYLI", "MOYNOQ", "QANLIKOL",
        "QONGIROT", "QORAOZAK", "SHUMANAY", "TAXTAKOPIR", "TORTKOL", "XOJAYLI", "TAXIATOSH",
        "BOZATOV"
    ],
    "NAMANGAN VILOYATI": [
        "NAMANGAN", "CHORTOQ", "CHUST", "KOSONSOY", "MINGBULOQ", "NORIN", "POP", "TORAQORGON",
        "UCHQORGON", "UYCHI", "YANGIQORGON"
    ],
    "FARGONA VILOYATI": [
        "FARGONA", "OLTIARIQ", "QOSHTEPA", "QUVA", "SOX", "TOSHLOQ", "YOZYOVON"
    ],
    "ANDIJON VILOYATI": [
        "ANDIJON", "ASAKA", "BALIQCHI", "BOSTON", "BULOQBOSHI", "IZBOSKAN", "JALAQUDUQ",
        "XOJAOBOD", "QORGONTEPA", "MARHAMAT", "OLTINKOL", "PAXTAOBOD", "SHAHRIXON", "ULUGNOR"
    ],
    "KOKAND VILOYATI": [
        "QOQAND", "BAGDOD", "BESHARIQ", "BUVAYDA", "DANGARA", "FURQAT", "RISHTON", "UCHKOPRIK",
        "OZBEKISTON"
    ],
}

# Округ -> районы
RAYON = {
    "AMUDARYO": [
        "AMUDARYO TUMANI"
    ],
    "ANDIJON": [
        "ANDIJON SHAHAR"
    ],
    "ANGOR": [
        "ANGOR TUMANI"
    ],
    "OHANGARON": [
        "ANGREN SHAHAR", "OLMALIQ SHAHAR", "OXANGARON TUMANI"
    ],
    "ASAKA": [
        "ASAKA TUMANI"
    ],
    "TASHKENT": [
        "ASKIYA BOZOR", "BEKTEMIR TUMANI", "CHILONZOR TUMANI", "ECO BOZOR", "FOOD CITY BOZOR",
        "GVARDEYSKIY BOZOR", "IPPODROM BOZOR", "KADISHEVA BOZOR", "MIROBOD BOZOR",
        "MIROBOD TUMANI", "MIRZO ULUGBEK TUMANI", "OLMAZOR TUMANI", "OLOY BOZOR", "QATORTOL BOZOR",
        "QOYLIQ BOZOR", "SAMPI BOZOR", "SERGELI TUMANI", "SHAYXONTOXUR TUMANI", "TOSHKENT SHAHAR",
        "UCHTEPA TUMANI", "UNIVERSAM BOZOR", "URIKZOR BOZOR", "VODIY BOZOR", "YAKKASAROY TUMANI",
        "YANGI BOZOR", "YANGIHAYOT TUMANI", "YASHNOBOD TUMANI", "YUNUSOBOD TUMANI"
    ],
    "BALIQCHI": [
        "BALIQCHI TUMANI", "JILONGU TUMANI"
    ],
    "BAXMAL": [
        "BAXMAL TUMANI"
    ],
    "BEKOBOD": [
        "BEKOBOD TUMANI"
    ],
    "BERUNIY": [
        "BERUNIY TUMANI"
    ],
    "BESHARIQ": [
        "BESHARIQ TUMANI"
    ],
    "KASBI": [
        "BESHKENT SHAHAR", "KASBI TUMANI"
    ],
    "BAGDOD": [
        "BOGDOD TUMANI"
    ],
    "BOGOT": [
        "BOGOT TUMANI"
    ],
    "BOKA": [
        "BOKA TUMANI", "ORTASAROY TUMANI"
    ],
    "BOSTONLIQ": [
        "BOSTONLIQ TUMANI", "GAZALKENT TUMANI"
    ],
    "BOSTON": [
        "BOZ TUMANI", "BUSTON TUMANI"
    ],
    "BULOQBOSHI": [
        "BULOQBOSHI TUMANI"
    ],
    "BULUNGUR": [
        "BULUNGUR TUMANI"
    ],
    "BUVAYDA": [
        "BUVAYDA TUMANI"
    ],
    "BUXORO": [
        "BUXORO SHAHAR", "GALASIYO TUMANI"
    ],
    "PAYARIQ": [
        "CHELAK TUMANI", "PAYARIQ TUMANI", "POYARIQ TUMANI"
    ],
    "CHIMBOY": [
        "CHIMBOY TUMANI", "SHIMBAU TUMANI"
    ],
    "JALAQUDUQ": [
        "CHINOBOD TUMANI", "JALAQUDUQ TUMANI"
    ],
    "CHINOZ": [
        "CHINOZ TUMANI"
    ],
    "CHIRCHIQ": [
        "CHIRCHIQ SHAHAR"
    ],
    "CHIROQCHI": [
        "CHIROQCHI TUMANI"
    ],
    "CHORTOQ": [
        "CHORTOQ TUMANI"
    ],
    "CHUST": [
        "CHUST TUMANI"
    ],
    "DANGARA": [
        "DANGARA TUMANI"
    ],
    "ZOMIN": [
        "DASHTAOBOD TUMANI", "ZOMIN TUMANI"
    ],
    "DEHQONOBOD": [
        "DEHQONOBOD TUMANI"
    ],
    "DENOV": [
        "DENOV TUMANI"
    ],
    "DOSTLIK": [
        "DUSTLIK TUMANI"
    ],
    "ELLIKQALA": [
        "ELLIKQALA TUMANI"
    ],
    "XOJAOBOD": [
        "ELOBOD SHAHAR", "XOJAOBOD TUMANI", "XONOBOT TUMANI", "XUDJAOBOD TUMANI"
    ],
    "FARGONA": [
        "FARGONA BOZOR", "FARGONA SHAHAR", "MARGILON TUMANI"
    ],
    "FURQAT": [
        "FURQAT TUMANI"
    ],
    "GALLAOROL": [
        "GALLAOROL TUMANI"
    ],
    "GIJDUVON": [
        "GIJDUVON SHAHAR"
    ],
    "GUZOR": [
        "GOZAL TUMANI", "GUZOR TUMANI"
    ],
    "GULISTON": [
        "GULISTON SHAHAR"
    ],
    "GURLAN": [
        "GURLAN TUMANI"
    ],
    "QOQAND": [
        "HAMZA SHAHAR", "QOQON SHAHAR", "XAMZA SHAHAR"
    ],
    "HAZORASP": [
        "HAZORASP TUMANI", "PITNAK TUMANI"
    ],
    "ISHTIXON": [
        "ISHTIXON TUMANI"
    ],
    "IZBOSKAN": [
        "IZBOSKAN TUMANI", "PAYTUG TUMANI"
    ],
    "JARQORGON": [
        "JARQORGON TUMANI"
    ],
    "JIZZAKH": [
        "JIZZAX SHAHAR"
    ],
    "JOMBOY": [
        "JOMBOY TUMANI"
    ],
    "JONDOR": [
        "JONDOR TUMANI"
    ],
    "PASTDARGOM": [
        "JUMA TUMANI", "PASTARGOM TUMANI"
    ],
    "KARMANA": [
        "KARMANA TUMANI"
    ],
    "KATTAQORGON": [
        "KATTAQORGON TUMANI"
    ],
    "ZANGIOTA": [
        "KELES SHAHAR", "ZANGIOTA TUMANI"
    ],
    "KITOB": [
        "KITOB TUMANI"
    ],
    "KOGON": [
        "KOGON TUMANI"
    ],
    "SAMARQAND": [
        "KONIGIL TUMANI", "OROMGOH TUMANI", "SAMARQAND SHAHAR"
    ],
    "KONIMEX": [
        "KONIMEX TUMANI"
    ],
    "QORAKOL": [
        "KORAKUL TUMANI", "QORAKOL TUMANI"
    ],
    "QOSHKOPIR": [
        "KOSHKOPIR TUMANI", "KUSHKUPIR TUMANI", "QOSHKUPIR TUMANI"
    ],
    "KOSON": [
        "KOSON TUMANI"
    ],
    "KOSONSOY": [
        "KOSONSOY TUMANI"
    ],
    "OLTINKOL": [
        "KUYGANYOR TUMANI", "OLTINKOL TUMANI"
    ],
    "BOYSUN": [
        "LOYISH TUMANI"
    ],
    "MARHAMAT": [
        "MARHAMAT TUMANI"
    ],
    "MUBORAK": [
        "MUBORAK TUMANI"
    ],
    "MUZRABOT": [
        "MUZRABOD TUMANI"
    ],
    "NAMANGAN": [
        "NAMANGAN SHAHAR"
    ],
    "NARPAY": [
        "NARPAY TUMANI"
    ],
    "NAVBAHOR": [
        "NAVBAHOR TUMANI"
    ],
    "NAVOIY": [
        "NAVOIY SHAHAR"
    ],
    "NISHON": [
        "NISHON TUMANI"
    ],
    "NORIN": [
        "NORIN TUMANI", "TOSHBULOQ SHAHAR"
    ],
    "NUKUS": [
        "NUKUS SHAHAR"
    ],
    "YANGIYOL": [
        "NURAFSHON TUMANI", "QUYICHIRCHIQ TUMANI", "YANGIYOL SHAHAR"
    ],
    "NUROBOD": [
        "NUROBOD TUMANI"
    ],
    "NUROTA": [
        "NUROTA TUMANI"
    ],
    "OLOT": [
        "OLOT TUMANI"
    ],
    "OLTIARIQ": [
        "OLTIARIQ TUMANI"
    ],
    "OQDARYO": [
        "OQDARYO TUMANI"
    ],
    "OQQORGON": [
        "OQQORGON TUMANI"
    ],
    "QIBRAY": [
        "ORTACHIRCHIQ TUMANI", "QIBRAY TUMANI", "TOSHKENT TUMANI", "URTACHIRCHIQ TUMANI",
        "YUQORICHIRCHIQ TUMANI"
    ],
    "OZBEKISTON": [
        "OZBEKISTON TUMANI"
    ],
    "PARKENT": [
        "PARKENT TUMANI", "ZARKENT TUMANI"
    ],
    "PAXTACHI": [
        "PAXTACHI TUMANI"
    ],
    "PAXTAKOR": [
        "PAXTAKOR TUMANI"
    ],
    "PAXTAOBOD": [
        "PAXTAOBOD TUMANI"
    ],
    "PESHKU": [
        "PESHKU TUMANI"
    ],
    "PISKENT": [
        "PISKENT TUMANI"
    ],
    "POP": [
        "POP TUMANI"
    ],
    "QAMASHI": [
        "QAMASHI TUMANI"
    ],
    "QARSHI": [
        "QARSHI SHAHAR", "QASHQADARYO"
    ],
    "QIZILTEPA": [
        "QIZILTEPA TUMANI"
    ],
    "QIZIRIQ": [
        "QIZIRIQ TUMANI"
    ],
    "QONGIROT": [
        "QONGIROT TUMANI"
    ],
    "QORGONTEPA": [
        "QORASUV TUMANI", "QORGONTEPA TUMANI", "QURGONTEPA TUMANI"
    ],
    "QOROVULBOZOR": [
        "QOROVULBOZOR TUMANI"
    ],
    "QUMQORGON": [
        "QUMQORGON TUMANI"
    ],
    "QUVA": [
        "QUVA SHAHAR", "QUVASOY TUMANI"
    ],
    "RISHTON": [
        "RISHTON TUMANI"
    ],
    "ROMITAN": [
        "ROMITAN TUMANI"
    ],
    "SARIOSIYO": [
        "SARIOSIYO TUMANI"
    ],
    "SHAHRIXON": [
        "SHAHRIHON TUMANI"
    ],
    "SHAHRISABZ": [
        "SHAHRISABZ SHAHAR"
    ],
    "SHAROF RASHIDOV": [
        "SHAROF RASHIDOV TUMANI"
    ],
    "SHEROBOD": [
        "SHEROBOD TUMANI"
    ],
    "SHOFIRKON": [
        "SHOFIRKON SHAHAR", "SHOFIRKON TUMANI"
    ],
    "SHUMANAY": [
        "SHOMANAY TUMANI"
    ],
    "SHOVOT": [
        "SHOVOT TUMANI"
    ],
    "SHORCHI": [
        "SHURCHI TUMANI"
    ],
    "SIRDARYO": [
        "SIRDARYO TUMANI"
    ],
    "TAXIATOSH": [
        "TAXIATOSH TUMANI"
    ],
    "TAXTAKOPIR": [
        "TAXTA KOPRIK"
    ],
    "TOYLOQ": [
        "TAYLOQ TUMANI", "TOYLOQ TUMANI"
    ],
    "TERMIZ": [
        "TERMEZ SHAHAR"
    ],
    "TORAQORGON": [
        "TORAQORGON TUMANI", "TURAQURGON TUMANI"
    ],
    "TORTKOL": [
        "TORTKOL TUMANI"
    ],
    "TOSHLOQ": [
        "TOSHLOQ TUMANI"
    ],
    "TUPROQQALA": [
        "TUPROQQALA TUMANI"
    ],
    "UCHKOPRIK": [
        "UCHKOPRIK TUMANI", "YAYPAN TUMANI"
    ],
    "UCHQORGON": [
        "UCHQORGON TUMANI"
    ],
    "UCHQUDUQ": [
        "UCHQUDUQ TUMANI"
    ],
    "ULUGNOR": [
        "ULUGNOR TUMANI"
    ],
    "URGANCH": [
        "URGANCH SHAHAR"
    ],
    "URGUT": [
        "URGUT TUMANI"
    ],
    "UYCHI": [
        "UYCHI TUMANI"
    ],
    "VOBKENT": [
        "VOBKENT TUMANI"
    ],
    "XATIRCHI": [
        "XATIRCHI TUMANI"
    ],
    "XIVA": [
        "XIVA SHAHAR"
    ],
    "XOJAYLI": [
        "XOJAYLI TUMANI"
    ],
    "XONQA": [
        "XONQA TUMANI"
    ],
    "YAKKABOG": [
        "YAKKABOG TUMANI"
    ],
    "YANGIARIQ": [
        "YANGIARIQ TUMANI"
    ],
    "YANGIBOZOR": [
        "YANGIBOZOR TUMANI"
    ],
    "YANGIQORGON": [
        "YANGIQURGON TUMANI"
    ],
    "YOZYOVON": [
        "YOZYOVON TUMANI"
    ],
    "ZAFAROBOD": [
        "ZAFAROBOD TUMANI"
    ],
    "ZARAFSHAN": [
        "ZARAFSHON SHAHAR"
    ],
}

# Область -> коды доставщика
DELIVERY_CODE = {
    "TASHKENT": [
        "D2-TOP1", "D3-TOP2", "D4-YNSBD-1", "D5-SHAYXTR", "D6-OLMAZR", "D7-CLNZR1", "D8-YNSBD-2",
        "D9-M.ULU1", "D10-UCHTEP", "D11-MIR.YK", "D12-CLNZR2", "D13-YASHNB", "D14-SRGILI",
        "D15-M.ULU2", "D16-CHRQ", "D17-CHRQ-1", "D18-CHRQ-2", "D19-OSDO-1", "D20-OSDO-2",
        "D21-OSDO-3", "D22-YANG-1", "D23-YANG-2", "D24-BEKBOD", "D25-ANG.OX", "D26-OLMALQ",
        "K1-KA", "K3-KA", "K5-KA"
    ],
    "TOSHKENT VILOYATI": [
        "D2-TOP1", "D3-TOP2", "D4-YNSBD-1", "D5-SHAYXTR", "D6-OLMAZR", "D7-CLNZR1", "D8-YNSBD-2",
        "D9-M.ULU1", "D10-UCHTEP", "D11-MIR.YK", "D12-CLNZR2", "D13-YASHNB", "D14-SRGILI",
        "D15-M.ULU2", "D16-CHRQ", "D17-CHRQ-1", "D18-CHRQ-2", "D19-OSDO-1", "D20-OSDO-2",
        "D21-OSDO-3", "D22-YANG-1", "D23-YANG-2", "D24-BEKBOD", "D25-ANG.OX", "D26-OLMALQ",
        "K1-KA", "K3-KA", "K5-KA"
    ],
    "SAMARQAND VILOYATI": [
        "SAM-D1", "SAM-D3", "SAM-D4", "SAM-D5", "SAM-D6", "SAM-D8", "SAM-D7"
    ],
    "BUXORO VILOYATI": [
        "BUX-D3", "BUX-D6", "BUX-D4", "BUX-D2", "BUX-D5", "BUX-D7", "BUX-D1"
    ],
    "NAVOIY VILOYATI": [
        "NAV-D2", "NAV-D1", "NAV-D3", "NAV-D4"
    ],
    "JIZZAX VILOYATI": [
        "JIZ-D2", "JIZ-D3", "JIZ-D1"
    ],
    "SURXANDARYO VILOYATI": [
        "TER-D1", "TER-DJ", "TER-D2"
    ],
    "QASHQADARYO VILOYATI": [
        "QAR-D3", "QAR-D1", "QAR-D2", "QAR-D4", "SHAH-D2", "SHAH-D1"
    ],
    "XORAZM VILOYATI": [
        "XOR-D4", "XOR-D2", "XOR-D5", "XOR-D3", "XOR-D1", "XOR-D6"
    ],
    "QORAQALPOQISTON": [
        "NUK-D3", "NUK-D2", "NUK-D1"
    ],
    "FARGONA VILOYATI": [
        "FAR-D1", "FAR-D2", "FAR-D3", "FAR-D4", "FAR-D5", "QOQ-D1", "QOQ-D2", "QOQ-D3", "QOQ-D4",
        "QOQ-D5"
    ],
    "ANDIJON VILOYATI": [
        "AND-D1", "AND-D2", "AND-D3", "AND-D4", "AND-D5"
    ],
    "NAMANGAN VILOYATI": [
        "NAM-D1", "NAM-D2", "NAM-D3", "NAM-D4", "NAM-D5"
    ],
    "DEFAULT": [
        "DLV-101", "DLV-102", "DLV-103"
    ],
}

FORMAT = ['Chain', 'Drogery', 'Hotels', 'OP.Markets', 'Others', 'Perfumery', 'Pharmacy', 'Super M.', 'Superettes', 'Web Sales']

CHANNEL = ['B.SALOONS', 'HORECA', 'MOD.TRADE', 'TRAD TRADE', 'WHOLESALE']

TYPE = ['FOOD', 'FOOD-HPC', 'HPC']

CATEGORY = ['A', 'B', 'C', 'D']

DAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница']


def get_rayons(okrug: str) -> list[str]:
    """
    Районы выбранного округа.

    Для 26 округов (SARDOBA, MOYNOQ, SOX и других) районов в исходном файле
    не оказалось. Чтобы выбор не упирался в пустой список, возвращаем сам
    округ — так же, как работала прежняя версия справочника, где районы
    дублировали округа. Как только районы для них появятся, просто добавь
    их в RAYON выше.
    """
    return RAYON.get(okrug) or ([okrug] if okrug else [])


def get_delivery_codes(oblast: str) -> list[str]:
    """Коды доставщика привязаны к области; для новых областей — общий список."""
    return DELIVERY_CODE.get(oblast, DELIVERY_CODE["DEFAULT"])
