# locations.py

CAMPUS_BUILDINGS = [
    "Abrams", "Administration", "AME", "Apache", "Art and Museum of Art",
    "AHSC", "AHSC Library", "Arbol de la Vida", "Arizona Stadium",
    "Arizona State Museum", "Babcock", "Bartlett", "Beal Center",
    "Bear Down Gym", "Bio-Sciences East", "Bio-Sciences West", "Speech",
    "Biomedical Research Lab", "Bookstore", "CALA West", "CALA East",
    "CALS Greenhouse", "Centennial", "CESL", "Chavez", "CHRP",
    "Chemical Sciences", "Chemistry", "Cherry Ave. Garage",
    "Civil Engineering", "Cochise", "Coconino", "Colonia de la Paz",
    "Communications", "Computer Center", "Comstock", "Corleone Center",
    "Coronado", "DeConcini ENRB", "Douglass", "Drachman", "Drama",
    "ECE", "Education", "Education North (EDC)", "El Portal", "Eller Theater", "Engineering",
    "Flandrau", "Forbes", "Geronimo", "Gila", "Gittings", "Gould-Simpson",
    "Graham", "Greenlee", "Harshbarger", "Harvill", "Haury", "Herring",
    "Highland Commons", "Highland Ave. Garage", "Hillenbrand Stadium",
    "Hopi", "Huachuca", "Jefferson Gymnasium", "Jimenez Field",
    "Kaibab", "Keating", "Koffler", "Kuiper", "La Aldea", "Law", "LSB",
    "Main Library", "Science & Engineering Library", "Life Sciences South",
    "Marshall", "Math", "Math East", "Maricopa", "Marley", "MLK",
    "McClelland", "McClelland Park", "McKale Memorial Center",
    "Medical Research", "Meinel", "Mines & Metallurgy", "Mirror Lab",
    "Modern Languages", "Mohave", "Music", "Navajo", "Nugent", "Nursing",
    "Old Main", "Pacheco ILC", "Park Ave. Garage", "PSU", "PAS(Phys-Atmos Sciences)", "C.A.T.S.",
    "Pharmacy", "Pima", "Pinal", "Police", "Psychology", "Pueblo de la Cienega",
    "RLAS", "Robson Tennis Center", "Saguaro", "Sancet Stadium", "Santa Cruz",
    "SALT Center", "Schaefer", "Shantz", "Sierra", "Sixth St. Garage",
    "Environment and Natural Resources 2","Hillenbrand Aquatic Center",
    "Social Sciences", "Sonora", "Steward Observatory", "Student Recreation Center",
    "North REC", "Honors", "Cactus Grill", "85 North", "Slot Canyon Café", "Radicchio",
    "Student Union Memorial Center", "Tyndall Ave. Garage", "Udall Center",
    "UAMC", "Vet. Sci. & Microbiology", "Visitor Center", "Yavapai", "Yuma",
    "Cole and Jeannie Davis Sports Center"
]

# mapping for common nicknames
ALIASES = {
    "sel": "Science & Engineering Library",
    "the sel": "Science & Engineering Library",
    "science library": "Science & Engineering Library",
    "scilib": "Science & Engineering Library",
    "rec center": "Student Recreation Center",
    "south rec": "Student Recreation Center",
    "the rec": "Student Recreation Center",
    "north rec": "Student Recreation Center",
    "the union": "Student Union Memorial Center",
    "student unions": "Student Union Memorial Center",
    "union": "Student Union Memorial Center",
    "sumc": "Student Union Memorial Center",
    "enrb": "DeConcini ENRB",
    "enr2": "Environment and Natural Resources 2",
    "gould": "Gould-Simpson",
    "gs": "Gould-Simpson",
    "triathlon hq": "Hillenbrand Aquatic Center",
    "cats": "C.A.T.S.",
    "c.a.t.s.": "C.A.T.S.",
    "clements": "C.A.T.S.",
    "Ginny L. Clements Academic Center": "C.A.T.S.",
    "mckale": "McKale Memorial Center",
    "mckale center": "McKale Memorial Center",
    "edc": "Education North (EDC)",
    "education north": "Education North (EDC)",
    "engineering design center": "Education North (EDC)",
    "la paz": "Colonia de la Paz",
    "civil": "Civil Engineering",
    "Physics-Atmospheric Sciences Building": "PAS(Phys-Atmos Sciences)",
    "pas": "PAS(Phys-Atmos Sciences)",
    "Center for English as a Second Language": "CESL",
    "M Pacheco ILC": "Pacheco ILC",
    "M. Pacheco ILC": "Pacheco ILC",
    "Manuel Pacheco Integrated Learning Center": "Pacheco ILC",
    "ILC": "Pacheco ILC",
    "slhs": "Speech"
}

def get_standard_name(name):
    """
    Normalizes input and checks both the official list AND the aliases.
    """
    # takes name above and standarizes it to avoid uppercase and special character differences
    clean_input = name.strip().lower().replace(" ", "").replace("-", "").replace(".", "")

    # iterate through the Aliases list
    # normalize the keys in the dictionary for comparison
    # if they match, return the OFFICIAL string from CAMPUS_BUILDINGS
    for nickname, official_name in ALIASES.items():
        clean_nickname = nickname.lower().replace(" ", "").replace("-", "").replace(".", "")
        if clean_input == clean_nickname:
            return official_name

    # iterate through the official list
    for building in CAMPUS_BUILDINGS:
        clean_building = building.lower().replace(" ", "").replace("-", "").replace(".", "")
        if clean_input == clean_building:
            return building
    # return None if no match is found (for WhyPhy.py)
    return None
