-- JEE WAR ROOM database dump
-- generated 2026-09-16T10:15:29.944769+00:00
PRAGMA foreign_keys=OFF;
BEGIN;

DROP TABLE IF EXISTS activities;
CREATE TABLE activities(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, type TEXT NOT NULL,
      custom_key TEXT, subject TEXT, chapter TEXT, amount INTEGER DEFAULT 0,
      duration INTEGER DEFAULT 0, extra TEXT, note TEXT, day TEXT NOT NULL, created_at TEXT NOT NULL);
DROP TABLE IF EXISTS app_settings;
CREATE TABLE app_settings(id INTEGER PRIMARY KEY CHECK (id=1), json TEXT NOT NULL DEFAULT '{}');
DROP TABLE IF EXISTS chapters;
CREATE TABLE chapters(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject TEXT NOT NULL,
      name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'not_started',
      hidden INTEGER NOT NULL DEFAULT 0, sort INTEGER NOT NULL DEFAULT 0, custom INTEGER NOT NULL DEFAULT 0, lectures_total INTEGER NOT NULL DEFAULT 0, lectures_done INTEGER NOT NULL DEFAULT 0);
DROP TABLE IF EXISTS errors;
CREATE TABLE errors(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject TEXT, chapter TEXT,
      etype TEXT, qno TEXT, note TEXT, day TEXT NOT NULL, created_at TEXT NOT NULL);
DROP TABLE IF EXISTS friendships;
CREATE TABLE friendships(
      user_id INTEGER NOT NULL, friend_id INTEGER NOT NULL, created_at TEXT,
      PRIMARY KEY(user_id, friend_id));
DROP TABLE IF EXISTS messages;
CREATE TABLE messages(
      id INTEGER PRIMARY KEY, sender INTEGER NOT NULL, recipient INTEGER NOT NULL,
      body TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT);
DROP TABLE IF EXISTS mocks;
CREATE TABLE mocks(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, test_type TEXT, total INTEGER DEFAULT 300,
      score REAL, phys REAL, chem REAL, math REAL, attempted INTEGER, correct INTEGER,
      incorrect INTEGER, day TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL);
DROP TABLE IF EXISTS nonces;
CREATE TABLE nonces(
      nonce TEXT PRIMARY KEY, user_id INTEGER, created_at REAL, result TEXT);
DROP TABLE IF EXISTS sessions;
CREATE TABLE sessions(
      token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT);
DROP TABLE IF EXISTS settings;
CREATE TABLE settings(user_id INTEGER PRIMARY KEY, json TEXT NOT NULL);
DROP TABLE IF EXISTS snapshots;
CREATE TABLE snapshots(
      user_id INTEGER NOT NULL, day TEXT NOT NULL, score REAL NOT NULL, air INTEGER NOT NULL,
      PRIMARY KEY(user_id, day));
DROP TABLE IF EXISTS targets;
CREATE TABLE targets(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, day TEXT NOT NULL, kind TEXT,
      subject TEXT, chapter TEXT, title TEXT NOT NULL, amount INTEGER, duration INTEGER,
      status TEXT NOT NULL DEFAULT 'open', created_at TEXT, completed_at TEXT);
DROP TABLE IF EXISTS timers;
CREATE TABLE timers(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject TEXT, chapter TEXT,
      started REAL NOT NULL, running INTEGER, ended_at TEXT, logged INTEGER DEFAULT 0);
DROP TABLE IF EXISTS users;
CREATE TABLE users(
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
      pass_hash TEXT NOT NULL, salt TEXT NOT NULL, code TEXT UNIQUE NOT NULL,
      partner_id INTEGER, exam_date TEXT, avatar_color TEXT, created_at TEXT, role TEXT NOT NULL DEFAULT 'user');
DROP TABLE IF EXISTS xp_events;
CREATE TABLE xp_events(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
      reason TEXT, ref_type TEXT, ref_id TEXT, day TEXT NOT NULL, created_at TEXT NOT NULL);
DROP INDEX IF EXISTS ix_act_day;
CREATE INDEX ix_act_day ON activities(user_id, day);
DROP INDEX IF EXISTS ix_err_day;
CREATE INDEX ix_err_day ON errors(user_id, day);
DROP INDEX IF EXISTS ix_mock_day;
CREATE INDEX ix_mock_day ON mocks(user_id, day);
DROP INDEX IF EXISTS ix_msg_pair;
CREATE INDEX ix_msg_pair ON messages(recipient, sender, id);
DROP INDEX IF EXISTS ix_t_day;
CREATE INDEX ix_t_day ON targets(user_id, day);

INSERT OR REPLACE INTO "activities" VALUES (1,2,'lecture',NULL,'Physics','Work, Energy and Power',0,0,NULL,'','2026-09-16','2026-09-16T02:59:40');
INSERT OR REPLACE INTO "app_settings" VALUES (1,'{"xp": {"lecture": 20, "dpp": 15, "pyq25": 25, "homework": 10, "revision30": 15, "mock": 50, "error": 25, "focus25": 10, "day100": 50, "distraction": -10, "missed": -20}, "weights": {"consistency": 25, "targets": 20, "practice": 20, "revision": 15, "mock": 20}, "streakThreshold": 70, "airEMA": 0.88, "activitiesEnabled": {"lecture": true, "dpp": true, "homework": true, "pyq": true, "revision": true, "mock": true, "error": true, "study": true, "distraction": true}}');
INSERT OR REPLACE INTO "chapters" VALUES (62,2,'Physics','Units and Measurements','not_started',0,0,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (63,2,'Physics','Kinematics','completed',0,10,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (64,2,'Physics','Laws of Motion','completed',0,20,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (65,2,'Physics','Work, Energy and Power','completed',0,30,0,0,1);
INSERT OR REPLACE INTO "chapters" VALUES (66,2,'Physics','Rotational Motion','not_started',0,50,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (67,2,'Physics','Gravitation','not_started',0,60,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (68,2,'Physics','Properties of Solids and Liquids','not_started',0,70,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (69,2,'Physics','Thermodynamics','not_started',0,80,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (70,2,'Physics','Kinetic Theory of Gases','not_started',0,90,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (71,2,'Physics','Oscillations and Waves','not_started',0,100,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (72,2,'Physics','Electrostatics','not_started',0,110,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (73,2,'Physics','Current Electricity','not_started',0,120,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (74,2,'Physics','Magnetic Effects of Current and Magnetism','not_started',0,130,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (75,2,'Physics','Electromagnetic Induction and Alternating Currents','not_started',0,140,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (76,2,'Physics','Electromagnetic Waves','not_started',0,150,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (77,2,'Physics','Optics','not_started',0,160,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (78,2,'Physics','Dual Nature of Matter and Radiation','not_started',0,170,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (79,2,'Physics','Atoms and Nuclei','not_started',0,180,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (80,2,'Physics','Electronic Devices','not_started',0,190,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (81,2,'Physics','Experimental Skills','not_started',0,200,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (82,2,'Chemistry','Some Basic Concepts of Chemistry','completed',0,20,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (83,2,'Chemistry','Atomic Structure','in_progress',0,21,0,11,4);
INSERT OR REPLACE INTO "chapters" VALUES (84,2,'Chemistry','Chemical Bonding and Molecular Structure','not_started',0,22,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (85,2,'Chemistry','Chemical Thermodynamics','not_started',0,23,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (86,2,'Chemistry','Solutions','not_started',0,24,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (87,2,'Chemistry','Equilibrium','not_started',0,25,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (88,2,'Chemistry','Redox Reactions and Electrochemistry','not_started',0,26,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (89,2,'Chemistry','Chemical Kinetics','not_started',0,27,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (90,2,'Chemistry','Classification of Elements and Periodicity','not_started',0,28,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (91,2,'Chemistry','p-Block Elements','not_started',0,29,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (92,2,'Chemistry','d- and f-Block Elements','not_started',0,30,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (93,2,'Chemistry','Coordination Compounds','not_started',0,31,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (94,2,'Chemistry','General Principles of Isolation of Metals','not_started',0,32,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (95,2,'Chemistry','Organic Chemistry — Basic Principles','not_started',0,33,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (96,2,'Chemistry','Hydrocarbons','not_started',0,34,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (97,2,'Chemistry','Organic Compounds Containing Halogens','not_started',0,35,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (98,2,'Chemistry','Alcohols, Phenols and Ethers','not_started',0,36,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (99,2,'Chemistry','Aldehydes, Ketones and Carboxylic Acids','not_started',0,37,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (100,2,'Chemistry','Organic Compounds Containing Nitrogen','not_started',0,38,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (101,2,'Chemistry','Biomolecules','not_started',0,39,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (102,2,'Chemistry','Polymers','not_started',0,40,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (103,2,'Chemistry','Chemistry in Everyday Life','not_started',0,41,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (104,2,'Mathematics','Relations and Functions','not_started',0,42,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (105,2,'Mathematics','Complex Numbers and Quadratic Equations','not_started',0,43,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (106,2,'Mathematics','Matrices and Determinants','not_started',0,44,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (107,2,'Mathematics','Permutations and Combinations','not_started',0,45,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (108,2,'Mathematics','Binomial Theorem','not_started',0,46,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (109,2,'Mathematics','Sequence and Series','not_started',0,47,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (110,2,'Mathematics','Trigonometric Functions','not_started',0,48,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (111,2,'Mathematics','Straight Lines','not_started',0,49,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (112,2,'Mathematics','Circles','not_started',0,50,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (113,2,'Mathematics','Conic Sections','not_started',0,51,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (114,2,'Mathematics','Three Dimensional Geometry','not_started',0,52,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (115,2,'Mathematics','Vector Algebra','not_started',0,53,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (116,2,'Mathematics','Limits, Continuity and Differentiability','not_started',0,54,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (117,2,'Mathematics','Applications of Derivatives','not_started',0,55,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (118,2,'Mathematics','Indefinite Integrals','not_started',0,56,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (119,2,'Mathematics','Definite Integrals and Applications','not_started',0,57,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (120,2,'Mathematics','Differential Equations','not_started',0,58,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (121,2,'Mathematics','Statistics','not_started',0,59,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (122,2,'Mathematics','Probability','not_started',0,60,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (123,3,'Physics','Units and Measurements','not_started',0,0,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (124,3,'Physics','Kinematics','not_started',0,1,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (125,3,'Physics','Laws of Motion','not_started',0,2,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (126,3,'Physics','Work, Energy and Power','not_started',0,3,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (127,3,'Physics','Rotational Motion','not_started',0,4,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (128,3,'Physics','Gravitation','not_started',0,5,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (129,3,'Physics','Properties of Solids and Liquids','not_started',0,6,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (130,3,'Physics','Thermodynamics','not_started',0,7,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (131,3,'Physics','Kinetic Theory of Gases','not_started',0,8,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (132,3,'Physics','Oscillations and Waves','not_started',0,9,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (133,3,'Physics','Electrostatics','not_started',0,10,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (134,3,'Physics','Current Electricity','not_started',0,11,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (135,3,'Physics','Magnetic Effects of Current and Magnetism','not_started',0,12,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (136,3,'Physics','Electromagnetic Induction and Alternating Currents','not_started',0,13,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (137,3,'Physics','Electromagnetic Waves','not_started',0,14,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (138,3,'Physics','Optics','not_started',0,15,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (139,3,'Physics','Dual Nature of Matter and Radiation','not_started',0,16,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (140,3,'Physics','Atoms and Nuclei','not_started',0,17,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (141,3,'Physics','Electronic Devices','not_started',0,18,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (142,3,'Physics','Experimental Skills','not_started',0,19,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (143,3,'Chemistry','Some Basic Concepts of Chemistry','not_started',0,20,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (144,3,'Chemistry','Atomic Structure','not_started',0,21,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (145,3,'Chemistry','Chemical Bonding and Molecular Structure','not_started',0,22,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (146,3,'Chemistry','Chemical Thermodynamics','not_started',0,23,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (147,3,'Chemistry','Solutions','not_started',0,24,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (148,3,'Chemistry','Equilibrium','not_started',0,25,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (149,3,'Chemistry','Redox Reactions and Electrochemistry','not_started',0,26,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (150,3,'Chemistry','Chemical Kinetics','not_started',0,27,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (151,3,'Chemistry','Classification of Elements and Periodicity','not_started',0,28,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (152,3,'Chemistry','p-Block Elements','not_started',0,29,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (153,3,'Chemistry','d- and f-Block Elements','not_started',0,30,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (154,3,'Chemistry','Coordination Compounds','not_started',0,31,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (155,3,'Chemistry','General Principles of Isolation of Metals','not_started',0,32,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (156,3,'Chemistry','Organic Chemistry — Basic Principles','not_started',0,33,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (157,3,'Chemistry','Hydrocarbons','not_started',0,34,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (158,3,'Chemistry','Organic Compounds Containing Halogens','not_started',0,35,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (159,3,'Chemistry','Alcohols, Phenols and Ethers','not_started',0,36,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (160,3,'Chemistry','Aldehydes, Ketones and Carboxylic Acids','not_started',0,37,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (161,3,'Chemistry','Organic Compounds Containing Nitrogen','not_started',0,38,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (162,3,'Chemistry','Biomolecules','not_started',0,39,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (163,3,'Chemistry','Polymers','not_started',0,40,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (164,3,'Chemistry','Chemistry in Everyday Life','not_started',0,41,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (165,3,'Mathematics','Relations and Functions','not_started',0,42,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (166,3,'Mathematics','Complex Numbers and Quadratic Equations','not_started',0,43,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (167,3,'Mathematics','Matrices and Determinants','not_started',0,44,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (168,3,'Mathematics','Permutations and Combinations','not_started',0,45,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (169,3,'Mathematics','Binomial Theorem','not_started',0,46,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (170,3,'Mathematics','Sequence and Series','not_started',0,47,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (171,3,'Mathematics','Trigonometric Functions','not_started',0,48,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (172,3,'Mathematics','Straight Lines','not_started',0,49,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (173,3,'Mathematics','Circles','not_started',0,50,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (174,3,'Mathematics','Conic Sections','not_started',0,51,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (175,3,'Mathematics','Three Dimensional Geometry','not_started',0,52,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (176,3,'Mathematics','Vector Algebra','not_started',0,53,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (177,3,'Mathematics','Limits, Continuity and Differentiability','not_started',0,54,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (178,3,'Mathematics','Applications of Derivatives','not_started',0,55,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (179,3,'Mathematics','Indefinite Integrals','not_started',0,56,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (180,3,'Mathematics','Definite Integrals and Applications','not_started',0,57,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (181,3,'Mathematics','Differential Equations','not_started',0,58,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (182,3,'Mathematics','Statistics','not_started',0,59,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (183,3,'Mathematics','Probability','not_started',0,60,0,0,0);
INSERT OR REPLACE INTO "chapters" VALUES (184,2,'Physics','Circular Motion','in_progress',0,40,1,5,2);
INSERT OR REPLACE INTO "nonces" VALUES ('43adcda4-27ab-4ef3-94ec-ada4678572f0',2,1789473279.2724361,'{"ok": true, "ids": [1, 2, 3, 4, 5, 6]}');
INSERT OR REPLACE INTO "nonces" VALUES ('lec-admin-test',3,1789476385.6350312,'{"ok": true, "id": 1, "xpGained": 20}');
INSERT OR REPLACE INTO "nonces" VALUES ('0e402055-a3f2-49e6-a58b-a71a5afb4eef',2,1789527580.2812436,'{"ok": true, "id": 1, "xpGained": 20}');
INSERT OR REPLACE INTO "sessions" VALUES ('983f59ebfa1674b9a267d8fc29203d46f53976c1430cad1f2fe77610de6ac0f6',2,'2026-09-16T02:25:03');
INSERT OR REPLACE INTO "sessions" VALUES ('7b745858d73bb77226e10c2f81466c9330bb6c63e22806a8e63f945602a10b7f',2,'2026-09-16T04:24:18');
INSERT OR REPLACE INTO "sessions" VALUES ('aa55a28c1cfcc8f5d55a499760bafd8fcb63de8b52c3f2f04f5dc63f5ef81820',2,'2026-09-16T05:01:45');
INSERT OR REPLACE INTO "sessions" VALUES ('4fae3b2090e3663cba6ee222c5aa585e5674bf8ead127671492df03c12d4b1e4',2,'2026-09-16T07:28:59');
INSERT OR REPLACE INTO "settings" VALUES (2,'{"xp": {"lecture": 20, "dpp": 15, "pyq25": 25, "homework": 10, "revision30": 15, "mock": 50, "error": 25, "focus25": 10, "day100": 50, "distraction": -10, "missed": -20}, "weights": {"consistency": 25, "targets": 20, "practice": 20, "revision": 15, "mock": 20}, "sharing": {"studyTime": true, "targets": true, "questions": true, "streak": true, "xp": true, "score": true, "air": true, "mocks": true, "notes": false, "errors": false, "reflections": false, "distractions": false}, "cards": [{"key": "air", "label": "Projected AIR", "enabled": true}, {"key": "execution", "label": "Today Execution", "enabled": true}, {"key": "time", "label": "Study Time", "enabled": true}, {"key": "questions", "label": "Questions", "enabled": true}, {"key": "streak", "label": "Streak", "enabled": true}, {"key": "xp", "label": "XP & Level", "enabled": true}, {"key": "friend", "label": "Friend Status", "enabled": true}, {"key": "syllabus", "label": "Syllabus Progress", "enabled": true}], "activities": [{"key": "lecture", "label": "Lecture", "emoji": "\ud83d\udcda", "kind": "lecture", "enabled": true}, {"key": "dpp", "label": "DPP", "emoji": "\ud83d\udcdd", "kind": "count", "enabled": true}, {"key": "homework", "label": "Homework", "emoji": "\ud83d\udcd6", "kind": "count", "enabled": true}, {"key": "pyq", "label": "PYQs", "emoji": "\ud83d\udd22", "kind": "count", "enabled": true}, {"key": "revision", "label": "Revision", "emoji": "\ud83d\udd04", "kind": "duration", "enabled": true}, {"key": "mock", "label": "Mock Test", "emoji": "\ud83e\uddea", "kind": "mock", "enabled": true}, {"key": "error", "label": "Error Analysis", "emoji": "\ud83e\udde0", "kind": "error", "enabled": true}, {"key": "study", "label": "Study Session", "emoji": "\u23f1\ufe0f", "kind": "duration", "enabled": true}, {"key": "distraction", "label": "Distraction", "emoji": "\ud83d\udcf1", "kind": "distraction", "enabled": true}], "customActivities": [], "metrics": [], "templates": [{"id": "normal", "name": "Normal Day", "items": [{"kind": "Lecture", "title": "Physics lecture", "subject": "Physics"}, {"kind": "Lecture", "title": "Chemistry lecture", "subject": "Chemistry"}, {"kind": "Lecture", "title": "Maths lecture", "subject": "Mathematics"}, {"kind": "DPP", "title": "2 DPPs", "subject": ""}, {"kind": "PYQs", "title": "50 PYQs", "subject": "", "amount": 50}, {"kind": "Revision", "title": "Revision session", "subject": ""}]}, {"id": "practice", "name": "Heavy Practice Day", "items": [{"kind": "PYQs", "title": "75 PYQs", "subject": "", "amount": 75}, {"kind": "DPP", "title": "3 DPPs", "subject": ""}, {"kind": "Revision", "title": "Revision + error book", "subject": ""}]}, {"id": "test", "name": "Test Day", "items": [{"kind": "Mock Test", "title": "Full mock test", "subject": ""}, {"kind": "Error Analysis", "title": "Analyse mock errors", "subject": ""}, {"kind": "Revision", "title": "Revise weak chapters", "subject": ""}]}, {"id": "revision", "name": "Revision Day", "items": [{"kind": "Revision", "title": "Physics revision (60m)", "subject": "Physics", "duration": 60}, {"kind": "Revision", "title": "Chemistry revision (60m)", "subject": "Chemistry", "duration": 60}, {"kind": "Revision", "title": "Maths revision (60m)", "subject": "Mathematics", "duration": 60}, {"kind": "PYQs", "title": "25 mixed PYQs", "subject": "", "amount": 25}]}], "streakThreshold": 70, "airEMA": 0.88, "bestStreak": 0, "sweepDay": "2026-09-16"}');
INSERT OR REPLACE INTO "settings" VALUES (3,'{"xp": {"lecture": 20, "dpp": 15, "pyq25": 20, "homework": 10, "revision30": 15, "mock": 50, "error": 25, "focus25": 10, "day100": 50, "distraction": -10, "missed": -20}, "weights": {"consistency": 25, "targets": 20, "practice": 20, "revision": 15, "mock": 20}, "sharing": {"studyTime": true, "targets": true, "questions": true, "streak": true, "xp": true, "score": true, "air": true, "mocks": true, "notes": false, "errors": false, "reflections": false, "distractions": false}, "cards": [{"key": "air", "label": "Projected AIR", "enabled": true}, {"key": "execution", "label": "Today Execution", "enabled": true}, {"key": "time", "label": "Study Time", "enabled": true}, {"key": "questions", "label": "Questions", "enabled": true}, {"key": "streak", "label": "Streak", "enabled": true}, {"key": "xp", "label": "XP & Level", "enabled": true}, {"key": "friend", "label": "Friend Status", "enabled": true}, {"key": "syllabus", "label": "Syllabus Progress", "enabled": true}], "activities": [{"key": "lecture", "label": "Lecture", "emoji": "\ud83d\udcda", "kind": "lecture", "enabled": true}, {"key": "dpp", "label": "DPP", "emoji": "\ud83d\udcdd", "kind": "count", "enabled": true}, {"key": "homework", "label": "Homework", "emoji": "\ud83d\udcd6", "kind": "count", "enabled": true}, {"key": "pyq", "label": "PYQs", "emoji": "\ud83d\udd22", "kind": "count", "enabled": true}, {"key": "revision", "label": "Revision", "emoji": "\ud83d\udd04", "kind": "duration", "enabled": true}, {"key": "mock", "label": "Mock Test", "emoji": "\ud83e\uddea", "kind": "mock", "enabled": true}, {"key": "error", "label": "Error Analysis", "emoji": "\ud83e\udde0", "kind": "error", "enabled": true}, {"key": "study", "label": "Study Session", "emoji": "\u23f1\ufe0f", "kind": "duration", "enabled": true}, {"key": "distraction", "label": "Distraction", "emoji": "\ud83d\udcf1", "kind": "distraction", "enabled": true}], "customActivities": [], "metrics": [], "templates": [{"id": "normal", "name": "Normal Day", "items": [{"kind": "Lecture", "title": "Physics lecture", "subject": "Physics"}, {"kind": "Lecture", "title": "Chemistry lecture", "subject": "Chemistry"}, {"kind": "Lecture", "title": "Maths lecture", "subject": "Mathematics"}, {"kind": "DPP", "title": "2 DPPs", "subject": ""}, {"kind": "PYQs", "title": "50 PYQs", "subject": "", "amount": 50}, {"kind": "Revision", "title": "Revision session", "subject": ""}]}, {"id": "practice", "name": "Heavy Practice Day", "items": [{"kind": "PYQs", "title": "75 PYQs", "subject": "", "amount": 75}, {"kind": "DPP", "title": "3 DPPs", "subject": ""}, {"kind": "Revision", "title": "Revision + error book", "subject": ""}]}, {"id": "test", "name": "Test Day", "items": [{"kind": "Mock Test", "title": "Full mock test", "subject": ""}, {"kind": "Error Analysis", "title": "Analyse mock errors", "subject": ""}, {"kind": "Revision", "title": "Revise weak chapters", "subject": ""}]}, {"id": "revision", "name": "Revision Day", "items": [{"kind": "Revision", "title": "Physics revision (60m)", "subject": "Physics", "duration": 60}, {"kind": "Revision", "title": "Chemistry revision (60m)", "subject": "Chemistry", "duration": 60}, {"kind": "Revision", "title": "Maths revision (60m)", "subject": "Mathematics", "duration": 60}, {"kind": "PYQs", "title": "25 mixed PYQs", "subject": "", "amount": 25}]}], "streakThreshold": 70, "airEMA": 0.88, "bestStreak": 0, "sweepDay": ""}');
INSERT OR REPLACE INTO "snapshots" VALUES (2,'2026-09-14',0.0,600000);
INSERT OR REPLACE INTO "snapshots" VALUES (3,'2026-09-15',0.0,600000);
INSERT OR REPLACE INTO "snapshots" VALUES (2,'2026-09-15',1.65,539871);
INSERT OR REPLACE INTO "snapshots" VALUES (2,'2026-09-16',3.32,485146);
INSERT OR REPLACE INTO "targets" VALUES (1,2,'2026-09-15','Lecture','Physics','','Physics lecture',NULL,NULL,'done','2026-09-15T11:54:39','2026-09-15T11:57:37');
INSERT OR REPLACE INTO "targets" VALUES (2,2,'2026-09-15','Lecture','Chemistry','','Chemistry lecture',NULL,NULL,'done','2026-09-15T11:54:39','2026-09-15T11:57:39');
INSERT OR REPLACE INTO "targets" VALUES (3,2,'2026-09-15','Lecture','Mathematics','','Maths lecture',NULL,NULL,'done','2026-09-15T11:54:39','2026-09-15T11:57:41');
INSERT OR REPLACE INTO "targets" VALUES (4,2,'2026-09-15','DPP','','','2 DPPs',NULL,NULL,'open','2026-09-15T11:54:39',NULL);
INSERT OR REPLACE INTO "targets" VALUES (6,2,'2026-09-15','Revision','','','Revision session',NULL,NULL,'open','2026-09-15T11:54:39',NULL);
INSERT OR REPLACE INTO "timers" VALUES (1,2,'Chemistry','Magnetic Effects of Current and Magnetism',1789532922.2466254,NULL,NULL,1);
INSERT OR REPLACE INTO "users" VALUES (2,'Yash','3a4466de9c830612fa341177f44c4a441573c434c2f07549eb34040253d9942d','ef0d5cb900b9dc4c02b2b9172d8485f37f818eec88f518868a5248c3faefb2a7','ALYWAY',NULL,'2027-01-21','#22d3ee','2026-09-15T11:53:55','admin');
INSERT OR REPLACE INTO "users" VALUES (3,'Anshkumar','b68067d098f69cc61357001ee49804e88db5607e58c18f0172275755c4469db4','04b4ea8f1d74dd75f64f84e17b07b39b598464847f5f9c7d388276966cc4e9c3','KQ8PIB',NULL,'2027-01-21','#818cf8','2026-09-15T11:55:38','user');
INSERT OR REPLACE INTO "xp_events" VALUES (1,2,20,'Lecture completed','activity','1','2026-09-16','2026-09-16T02:59:40');
COMMIT;
PRAGMA foreign_keys=ON;
