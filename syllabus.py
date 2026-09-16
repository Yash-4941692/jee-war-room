# Preloaded JEE Main syllabus (current PCM chapter structure).
# Users never have to type chapter names. Editable/hideable per account.

SUBJECTS = ["Physics", "Chemistry", "Mathematics"]

CHAPTERS = {
    "Physics": [
        "Units and Measurements",
        "Kinematics",
        "Laws of Motion",
        "Work, Energy and Power",
        "Rotational Motion",
        "Gravitation",
        "Properties of Solids and Liquids",
        "Thermodynamics",
        "Kinetic Theory of Gases",
        "Oscillations and Waves",
        "Electrostatics",
        "Current Electricity",
        "Magnetic Effects of Current and Magnetism",
        "Electromagnetic Induction and Alternating Currents",
        "Electromagnetic Waves",
        "Optics",
        "Dual Nature of Matter and Radiation",
        "Atoms and Nuclei",
        "Electronic Devices",
        "Experimental Skills",
    ],
    "Chemistry": [
        "Some Basic Concepts of Chemistry",
        "Atomic Structure",
        "Chemical Bonding and Molecular Structure",
        "Chemical Thermodynamics",
        "Solutions",
        "Equilibrium",
        "Redox Reactions and Electrochemistry",
        "Chemical Kinetics",
        "Classification of Elements and Periodicity",
        "p-Block Elements",
        "d- and f-Block Elements",
        "Coordination Compounds",
        "General Principles of Isolation of Metals",
        "Organic Chemistry — Basic Principles",
        "Hydrocarbons",
        "Organic Compounds Containing Halogens",
        "Alcohols, Phenols and Ethers",
        "Aldehydes, Ketones and Carboxylic Acids",
        "Organic Compounds Containing Nitrogen",
        "Biomolecules",
        "Polymers",
        "Chemistry in Everyday Life",
    ],
    "Mathematics": [
        "Relations and Functions",
        "Complex Numbers and Quadratic Equations",
        "Matrices and Determinants",
        "Permutations and Combinations",
        "Binomial Theorem",
        "Sequence and Series",
        "Trigonometric Functions",
        "Straight Lines",
        "Circles",
        "Conic Sections",
        "Three Dimensional Geometry",
        "Vector Algebra",
        "Limits, Continuity and Differentiability",
        "Applications of Derivatives",
        "Indefinite Integrals",
        "Definite Integrals and Applications",
        "Differential Equations",
        "Statistics",
        "Probability",
    ],
}

# Distraction categories
DISTRACTIONS = ["YouTube", "Instagram", "Gaming", "Phone", "Chatting", "Random browsing", "Other"]

# Error types
ERROR_TYPES = ["Concept", "Calculation", "Silly mistake", "Misread", "Guess", "Time pressure", "Formula", "Other"]

# Test types
TEST_TYPES = ["Full JEE Main", "Part Test", "Coaching Test", "Custom"]

# Quick-log activity definitions (order matters). kind: count | duration | lecture | mock | error
ACTIVITY_TYPES = [
    {"key": "lecture",       "label": "Lecture",        "emoji": "📚", "kind": "lecture",  "enabled": True},
    {"key": "dpp",           "label": "DPP",            "emoji": "📝", "kind": "count",    "enabled": True},
    {"key": "homework",      "label": "Homework",       "emoji": "📖", "kind": "count",    "enabled": True},
    {"key": "pyq",           "label": "PYQs",           "emoji": "🔢", "kind": "count",    "enabled": True},
    {"key": "revision",      "label": "Revision",       "emoji": "🔄", "kind": "duration", "enabled": True},
    {"key": "mock",          "label": "Mock Test",      "emoji": "🧪", "kind": "mock",     "enabled": True},
    {"key": "error",         "label": "Error Analysis", "emoji": "🧠", "kind": "error",    "enabled": True},
    {"key": "study",         "label": "Study Session",  "emoji": "⏱️", "kind": "duration", "enabled": True},
    {"key": "distraction",   "label": "Distraction",    "emoji": "📱", "kind": "distraction", "enabled": True},
]

DEFAULT_XP = {
    "lecture": 20,
    "dpp": 15,           # per completed DPP set logged
    "pyq25": 20,         # per 25 PYQs (50 -> 35, 75 -> 55, 100 -> 70)
    "homework": 10,
    "revision30": 15,    # per 30 minutes
    "mock": 50,
    "error": 25,
    "focus25": 10,       # per 25 focus-timer minutes
    "day100": 50,        # all targets done in a day
    "distraction": -10,
    "missed": -20,
}

DEFAULT_WEIGHTS = {"consistency": 25, "targets": 20, "practice": 20, "revision": 15, "mock": 20}

DEFAULT_SHARING = {
    "studyTime": True, "targets": True, "questions": True, "streak": True,
    "xp": True, "score": True, "air": True, "mocks": True,
    "notes": False, "errors": False, "reflections": False, "distractions": False,
}

DASHBOARD_CARDS = [
    {"key": "air",       "label": "Projected AIR",  "enabled": True},
    {"key": "execution", "label": "Today Execution","enabled": True},
    {"key": "time",      "label": "Study Time",     "enabled": True},
    {"key": "questions", "label": "Questions",      "enabled": True},
    {"key": "streak",    "label": "Streak",         "enabled": True},
    {"key": "xp",        "label": "XP & Level",     "enabled": True},
    {"key": "friend",    "label": "Friend Status",  "enabled": True},
    {"key": "syllabus",  "label": "Syllabus Progress","enabled": True},
]

TARGET_TEMPLATES = [
    {"id": "normal",  "name": "Normal Day", "items": [
        {"kind": "Lecture", "title": "Physics lecture", "subject": "Physics"},
        {"kind": "Lecture", "title": "Chemistry lecture", "subject": "Chemistry"},
        {"kind": "Lecture", "title": "Maths lecture", "subject": "Mathematics"},
        {"kind": "DPP", "title": "2 DPPs", "subject": ""},
        {"kind": "PYQs", "title": "50 PYQs", "subject": "", "amount": 50},
        {"kind": "Revision", "title": "Revision session", "subject": ""},
    ]},
    {"id": "practice", "name": "Heavy Practice Day", "items": [
        {"kind": "PYQs", "title": "75 PYQs", "subject": "", "amount": 75},
        {"kind": "DPP", "title": "3 DPPs", "subject": ""},
        {"kind": "Revision", "title": "Revision + error book", "subject": ""},
    ]},
    {"id": "test", "name": "Test Day", "items": [
        {"kind": "Mock Test", "title": "Full mock test", "subject": ""},
        {"kind": "Error Analysis", "title": "Analyse mock errors", "subject": ""},
        {"kind": "Revision", "title": "Revise weak chapters", "subject": ""},
    ]},
    {"id": "revision", "name": "Revision Day", "items": [
        {"kind": "Revision", "title": "Physics revision (60m)", "subject": "Physics", "duration": 60},
        {"kind": "Revision", "title": "Chemistry revision (60m)", "subject": "Chemistry", "duration": 60},
        {"kind": "Revision", "title": "Maths revision (60m)", "subject": "Mathematics", "duration": 60},
        {"kind": "PYQs", "title": "25 mixed PYQs", "subject": "", "amount": 25},
    ]},
]
