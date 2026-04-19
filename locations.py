# locations.py

CAMPUS_BUILDINGS = [
    "Abrams", "Administration", "AME", "Apache", "Art and Museum of Art",
    "AHSC", "AHSC Library", "Arbol de la Vida", "Arizona Stadium",
    "Arizona State Museum", "Babcock", "Bartlett", "Beal Center",
    "Bear Down Gym", "Bio-Sciences East", "Bio-Sciences West",
    "Biomedical Research Lab", "Bookstore", "CALA West", "CALA East",
    "CALS Greenhouse", "Centennial", "CESL", "Chavez", "CHRP",
    "Chemical Sciences", "Chemistry", "Cherry Ave. Garage",
    "Civil Engineering", "Cochise", "Coconino", "Colonia de la Paz",
    "Communications", "Computer Center", "Comstock", "Corleone Center",
    "Coronado", "DeConcini ENRB", "Douglass", "Drachman", "Drama",
    "ECE", "Education", "El Portal", "Eller Theater", "Engineering",
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
    "Old Main", "Pacheco ILC", "Park Ave. Garage", "PSU", "PAS",
    "Pharmacy", "Pima", "Pinal", "Police", "Psychology", "Pueblo de la Cienega",
    "RLAS", "Robson Tennis Center", "Saguaro", "Sancet Stadium", "Santa Cruz",
    "SALT Center", "Schaefer", "Shantz", "Sierra", "Sixth St. Garage",
    "Social Sciences", "Sonora", "Steward Observatory", "Student Recreation Center",
    "Student Union Memorial Center", "Tyndall Ave. Garage", "Udall Center",
    "UAMC", "Vet. Sci. & Microbiology", "Visitor Center", "Yavapai", "Yuma"
]


# takes name above and standarizes it to avoid uppercase and special character differences
def is_valid_location(name):
    # remove extra spaces and make it lowercase
    clean_name = name.strip().lower().replace(" ", "").replace("-", "")
    # do the same to every building in the campus list
    for building in CAMPUS_BUILDINGS:
        clean_building = building.lower().replace(" ", "").replace("-", "")
        if clean_name == clean_building:
            return True
    return False
