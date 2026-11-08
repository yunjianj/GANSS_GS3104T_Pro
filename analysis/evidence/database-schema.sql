CREATE TABLE IF NOT EXISTS t_config_data(config_id INTEGER PRIMARY KEY AUTOINCREMENT,           profile INTEGER, key TEXT, value TEXT);

CREATE TABLE IF NOT EXISTS t_profile_data(profile INTEGER PRIMARY KEY AUTOINCREMENT,           name TEXT, status INTEGER, app TEXT, type INTEGER);

CREATE TABLE IF NOT EXISTS t_key_macro_data(key_id INTEGER PRIMARY KEY AUTOINCREMENT,         profile INTEGER, fn_layer INTEGER, key_code INTEGER, layout_value INTEGER, layout_desc TEXT,         macro_type INTEGER, macro_value INTEGER, macro_value2 INTEGER, macro_value3 INTEGER, macro_desc TEXT);

CREATE TABLE IF NOT EXISTS t_key_otherdata(macro_value INTEGER PRIMARY KEY AUTOINCREMENT,           macro_desc TEXT, param TEXT, type INTEGER);

CREATE TABLE IF NOT EXISTS t_macro_data(macro_id INTEGER PRIMARY KEY AUTOINCREMENT,           play_times INTEGER, delay_type INTEGER, delay_time INTEGER, link_count INTEGER, name TEXT);

CREATE TABLE IF NOT EXISTS t_macrorecord(record_id INTEGER PRIMARY KEY AUTOINCREMENT,           macro_id INTEGER, type INTEGER, record_index INTEGER, desc TEXT, value INTEGER, delay_time INTEGER);

CREATE TABLE IF NOT EXISTS t_light_data(light_id INTEGER PRIMARY KEY AUTOINCREMENT,             profile INTEGER, name TEXT, mode INTEGER, brightness INTEGER, speed INTEGER,           direction INTEGER, colorful INTEGER, colorindex INTEGER,           color_value INTEGER, config_func INTEGER, reserved INTEGER, status INTEGER);

CREATE TABLE IF NOT EXISTS t_userlight_data(userlight_id INTEGER PRIMARY KEY AUTOINCREMENT,           profile INTEGER, name TEXT, speed INTEGER, brightness INTEGER, status INTEGER);

CREATE TABLE IF NOT EXISTS t_userlightrgb_data(userlightrgb_id INTEGER PRIMARY KEY AUTOINCREMENT,            profile INTEGER, userlight_id INTEGER, key_code INTEGER, key_rgb INTEGER);

CREATE TABLE IF NOT EXISTS t_customlight_data(customlight_id INTEGER PRIMARY KEY AUTOINCREMENT,           profile INTEGER, name TEXT, speed INTEGER, brightness INTEGER, status INTEGER);

CREATE TABLE IF NOT EXISTS t_customrgb_data(customrgb_id INTEGER PRIMARY KEY AUTOINCREMENT,            customlight_id INTEGER, keycode INTEGER, rgbvalue INTEGER, light_mode INTEGER,            colortype INTEGER, light_type INTEGER);

CREATE TABLE IF NOT EXISTS t_musiclayer_data(layer_id INTEGER PRIMARY KEY AUTOINCREMENT,            profile INTEGER, name TEXT, deviceindex INTEGER, foremode INTEGER, fore_amplitude INTEGER,           backmode INTEGER, backb_right INTEGER, sel INTEGER);

CREATE TABLE IF NOT EXISTS t_ledlayer_data(led_id INTEGER PRIMARY KEY AUTOINCREMENT,            profile INTEGER, type INTEGER, name TEXT, frame_size INTEGER, file_dir TEXT, first_file TEXT);

CREATE TABLE IF NOT EXISTS t_ledframe_data(frame_id INTEGER PRIMARY KEY AUTOINCREMENT,            led_id INTEGER, name TEXT, delay_time INTEGER, frame_index INTEGER, file_name TEXT);
