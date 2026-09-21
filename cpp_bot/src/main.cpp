#include <curl/curl.h>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <map>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <tuple>
#include <variant>
#include <vector>

namespace {

// Small dependency-free JSON reader. It is sufficient for Telegram and Sheets
// responses and keeps the native variant easy to build on a fresh Ubuntu host.
struct Json {
    using Object = std::map<std::string, Json>;
    using Array = std::vector<Json>;
    std::variant<std::nullptr_t, bool, double, std::string, Object, Array> value;
    Json() : value(nullptr) {}
    Json(bool x) : value(x) {}
    Json(double x) : value(x) {}
    Json(std::string x) : value(std::move(x)) {}
    Json(Object x) : value(std::move(x)) {}
    Json(Array x) : value(std::move(x)) {}
    bool object() const { return std::holds_alternative<Object>(value); }
    bool array() const { return std::holds_alternative<Array>(value); }
    const Object& obj() const { static const Object empty; return object() ? std::get<Object>(value) : empty; }
    const Array& arr() const { static const Array empty; return array() ? std::get<Array>(value) : empty; }
    std::string str(const std::string& fallback = "") const {
        if (std::holds_alternative<std::string>(value)) return std::get<std::string>(value);
        if (std::holds_alternative<double>(value)) { std::ostringstream out; out << std::get<double>(value); return out.str(); }
        if (std::holds_alternative<bool>(value)) return std::get<bool>(value) ? "true" : "false";
        return fallback;
    }
    long number(long fallback = 0) const { return std::holds_alternative<double>(value) ? static_cast<long>(std::get<double>(value)) : fallback; }
    const Json& operator[](const std::string& key) const { static const Json empty; auto it = obj().find(key); return it == obj().end() ? empty : it->second; }

    struct Parser {
        const std::string& text; size_t pos = 0;
        void ws() { while (pos < text.size() && std::isspace(static_cast<unsigned char>(text[pos]))) ++pos; }
        char take() { if (pos >= text.size()) throw std::runtime_error("unexpected end of JSON"); return text[pos++]; }
        void expect(char c) { ws(); if (take() != c) throw std::runtime_error("invalid JSON"); }
        std::string quoted() {
            expect('"'); std::string out;
            while (pos < text.size()) { char c = take(); if (c == '"') return out; if (c != '\\') { out += c; continue; } char e = take();
                if (e == '"' || e == '\\' || e == '/') out += e; else if (e == 'n') out += '\n'; else if (e == 'r') out += '\r'; else if (e == 't') out += '\t'; else if (e == 'b') out += '\b'; else if (e == 'f') out += '\f'; else if (e == 'u') { pos = std::min(pos + 4, text.size()); out += '?'; } }
            throw std::runtime_error("unterminated JSON string");
        }
        Json parse() {
            ws(); if (pos >= text.size()) throw std::runtime_error("empty JSON");
            if (text[pos] == '"') return Json(quoted());
            if (text[pos] == '{') { ++pos; Object out; ws(); if (pos < text.size() && text[pos] == '}') { ++pos; return Json(out); }
                while (true) { ws(); auto key = quoted(); expect(':'); out.emplace(std::move(key), parse()); ws(); if (take() == '}') break; } return Json(out); }
            if (text[pos] == '[') { ++pos; Array out; ws(); if (pos < text.size() && text[pos] == ']') { ++pos; return Json(out); }
                while (true) { out.push_back(parse()); ws(); if (take() == ']') break; } return Json(out); }
            if (text.compare(pos, 4, "true") == 0) { pos += 4; return Json(true); }
            if (text.compare(pos, 5, "false") == 0) { pos += 5; return Json(false); }
            if (text.compare(pos, 4, "null") == 0) { pos += 4; return Json(); }
            auto start = pos; while (pos < text.size() && std::string("-+.0123456789eE").find(text[pos]) != std::string::npos) ++pos; return Json(std::stod(text.substr(start, pos - start)));
        }
    };
    static Json parse(const std::string& text) { return Parser{text}.parse(); }
};

std::map<std::string, std::string> settings;
std::string get(const std::string& name, const std::string& fallback = "") { auto it = settings.find(name); return it == settings.end() ? fallback : it->second; }
void load_env() {
    std::ifstream file(".env"); std::string line;
    while (std::getline(file, line)) { if (line.empty() || line[0] == '#') continue; auto equal = line.find('='); if (equal == std::string::npos) continue; auto key = line.substr(0, equal); auto value = line.substr(equal + 1); if (value.size() >= 2 && value.front() == '"' && value.back() == '"') value = value.substr(1, value.size() - 2); settings[key] = value; }
}
size_t write_body(char* ptr, size_t size, size_t count, void* target) { static_cast<std::string*>(target)->append(ptr, size * count); return size * count; }
std::string request(const std::string& url, const std::string& post = "", const std::vector<std::string>& headers = {}) {
    CURL* curl = curl_easy_init(); if (!curl) throw std::runtime_error("libcurl unavailable"); std::string body; curl_slist* list = nullptr;
    for (const auto& h : headers) list = curl_slist_append(list, h.c_str()); if (!list) list = curl_slist_append(list, "Accept: */*");
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str()); curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_body); curl_easy_setopt(curl, CURLOPT_WRITEDATA, &body); curl_easy_setopt(curl, CURLOPT_HTTPHEADER, list); curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L); curl_easy_setopt(curl, CURLOPT_TIMEOUT, 60L); curl_easy_setopt(curl, CURLOPT_USERAGENT, "SCGPMessageBot-cpp/1.0");
    if (!post.empty()) { curl_easy_setopt(curl, CURLOPT_POST, 1L); curl_easy_setopt(curl, CURLOPT_POSTFIELDS, post.c_str()); }
    auto code = curl_easy_perform(curl); long status = 0; curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status); curl_slist_free_all(list); curl_easy_cleanup(curl);
    if (code != CURLE_OK) throw std::runtime_error(curl_easy_strerror(code)); if (status >= 400) throw std::runtime_error("HTTP " + std::to_string(status) + ": " + body.substr(0, 300)); return body;
}
std::string encode(const std::string& text) { CURL* curl = curl_easy_init(); if (!curl) return text; char* p = curl_easy_escape(curl, text.c_str(), static_cast<int>(text.size())); std::string out = p ? p : text; curl_free(p); curl_easy_cleanup(curl); return out; }
std::string escape_html(const std::string& text) { std::string out; for (char c : text) { if (c == '&') out += "&amp;"; else if (c == '<') out += "&lt;"; else if (c == '>') out += "&gt;"; else if (c == '"') out += "&quot;"; else out += c; } return out; }
std::string lower(std::string text) { std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); }); return text; }
std::vector<std::string> csv(const std::string& text) { std::vector<std::string> out; std::stringstream stream(text); std::string item; while (std::getline(stream, item, ',')) if (!item.empty()) out.push_back(item); return out; }

struct Event { std::string date, title, speaker, affiliation, time, location, description, link, source; };
bool operator<(const Event& a, const Event& b) { return std::tie(a.date, a.time, a.title, a.speaker) < std::tie(b.date, b.time, b.title, b.speaker); }
std::string today() { std::time_t now = std::time(nullptr); std::tm tm = *std::localtime(&now); std::ostringstream out; out << std::put_time(&tm, "%Y-%m-%d"); return out.str(); }
std::string add_days(const std::string& date, int days) { std::tm tm{}; std::istringstream(date) >> std::get_time(&tm, "%Y-%m-%d"); tm.tm_hour = 12; std::mktime(&tm); tm.tm_mday += days; std::mktime(&tm); std::ostringstream out; out << std::put_time(&tm, "%Y-%m-%d"); return out.str(); }
std::string monday(const std::string& date) { std::tm tm{}; std::istringstream(date) >> std::get_time(&tm, "%Y-%m-%d"); tm.tm_hour = 12; std::mktime(&tm); int day = tm.tm_wday == 0 ? 6 : tm.tm_wday - 1; return add_days(date, -day); }
std::string date_value(std::string value) {
    std::smatch m; value = std::regex_replace(value, std::regex("^\\s+|\\s+$"), "");
    if (std::regex_match(value, m, std::regex("(\\d{4})[-/](\\d{1,2})[-/](\\d{1,2}).*"))) { return m[1].str() + "-" + (m[2].str().size() == 1 ? "0" : "") + m[2].str() + "-" + (m[3].str().size() == 1 ? "0" : "") + m[3].str(); }
    if (std::regex_match(value, m, std::regex("(\\d{1,2})[/-](\\d{1,2})(?:[/-](\\d{2,4}))?.*"))) { int year = m[3].matched ? std::stoi(m[3].str()) : std::stoi(today().substr(0, 4)); if (year < 100) year += 2000; std::ostringstream out; out << year << '-' << std::setw(2) << std::setfill('0') << std::stoi(m[1].str()) << '-' << std::setw(2) << std::stoi(m[2].str()); return out.str(); }
    return "";
}
std::string json_escape(const std::string& text) { std::string out; for (char c : text) { if (c == '"') out += "\\\""; else if (c == '\\') out += "\\\\"; else if (c == '\n') out += "\\n"; else if (c == '\r') out += "\\r"; else out += c; } return out; }

std::string token() {
    auto value = get("GOOGLE_ACCESS_TOKEN"); if (!value.empty()) return value; FILE* pipe = popen("gcloud auth application-default print-access-token 2>/dev/null", "r"); if (!pipe) return ""; char buffer[256]; while (fgets(buffer, sizeof(buffer), pipe)) value += buffer; pclose(pipe); while (!value.empty() && std::isspace(static_cast<unsigned char>(value.back()))) value.pop_back(); return value;
}
std::string row_cell(const Json& row, size_t index) { return index < row.arr().size() ? row.arr()[index].str() : ""; }
std::vector<Event> sheet(const std::string& id, const std::string& source, const std::string& default_time, const std::string& default_location) {
    auto access = token(); if (access.empty()) throw std::runtime_error("Google access token unavailable"); auto response = request("https://sheets.googleapis.com/v4/spreadsheets/" + encode(id) + "/values/A:ZZ", "", {"Authorization: Bearer " + access}); auto rows = Json::parse(response)["values"].arr(); if (rows.empty()) return {};
    std::map<std::string, size_t> headers; for (size_t i = 0; i < rows[0].arr().size(); ++i) headers[lower(row_cell(rows[0], i))] = i;
    auto find = [&](std::initializer_list<const char*> names) { for (auto name : names) { auto it = headers.find(name); if (it != headers.end()) return it->second; } return SIZE_MAX; };
    size_t date_i = find({"date", "dates", "day", "date of seminar"}), start_i = find({"start", "start date", "starts"}), title_i = find({"title", "talk title", "name of talk"}), speaker_i = find({"speaker", "name", "presenter"}), affiliation_i = find({"affiliation", "institution"}), abstract_i = find({"abstract", "talk abstract", "description", "details"}), time_i = find({"time", "start time"}), location_i = find({"location", "room", "where"}), publish_i = find({"publish", "published", "visible"});
    if (title_i == SIZE_MAX || (date_i == SIZE_MAX && start_i == SIZE_MAX)) return {}; std::vector<Event> out;
    for (size_t i = 1; i < rows.size(); ++i) { auto publish = lower(publish_i == SIZE_MAX ? "true" : row_cell(rows[i], publish_i)); if (publish == "false" || publish == "no" || publish == "0") continue; auto start = start_i == SIZE_MAX ? "" : row_cell(rows[i], start_i); auto date = date_value(start.empty() ? row_cell(rows[i], date_i) : start); if (date.empty()) continue; Event e{date, row_cell(rows[i], title_i), speaker_i == SIZE_MAX ? "" : row_cell(rows[i], speaker_i), affiliation_i == SIZE_MAX ? "" : row_cell(rows[i], affiliation_i), time_i == SIZE_MAX ? default_time : row_cell(rows[i], time_i), location_i == SIZE_MAX ? default_location : row_cell(rows[i], location_i), abstract_i == SIZE_MAX ? "" : row_cell(rows[i], abstract_i), "", source}; if (e.time.empty() && start.size() >= 16) e.time = start.substr(11, 5); if (!e.title.empty() || !e.description.empty()) out.push_back(std::move(e)); }
    return out;
}

std::string ics_field(const std::string& block, const std::string& field) {
    std::smatch match; std::regex expression("(?:^|\\n)" + field + "(?:;[^:]*)?:([^\\n\\r]*)"); return std::regex_search(block, match, expression) ? match[1].str() : "";
}
int weekday(const std::string& date) { std::tm tm{}; std::istringstream(date) >> std::get_time(&tm, "%Y-%m-%d"); tm.tm_hour = 12; std::mktime(&tm); return tm.tm_wday; }
std::vector<Event> yitp(const std::string& url) {
    auto text = request(url); std::vector<Event> out; size_t pos = 0;
    while ((pos = text.find("BEGIN:VEVENT", pos)) != std::string::npos) { auto end = text.find("END:VEVENT", pos); if (end == std::string::npos) break; auto block = text.substr(pos, end - pos); pos = end + 10; auto start = ics_field(block, "DTSTART"); auto summary = ics_field(block, "SUMMARY"); if (start.size() < 8 || summary.empty()) continue;
        auto date = start.substr(0, 4) + "-" + start.substr(4, 2) + "-" + start.substr(6, 2); auto time = start.size() >= 13 ? start.substr(9, 2) + ":" + start.substr(11, 2) : ""; auto rule = ics_field(block, "RRULE"); auto last = add_days(today(), 370); std::smatch until; if (std::regex_search(rule, until, std::regex("UNTIL=(\\d{8})"))) last = until[1].str().substr(0, 4) + "-" + until[1].str().substr(4, 2) + "-" + until[1].str().substr(6, 2);
        auto location = ics_field(block, "LOCATION"), description = ics_field(block, "DESCRIPTION");
        for (auto day = date; day <= last && day <= add_days(today(), 370); day = add_days(day, 1)) { bool include = day == date; if (rule.find("FREQ=WEEKLY") != std::string::npos) include = weekday(day) == weekday(date); if (include && day >= add_days(today(), -30)) out.push_back({day, summary, "", "", time, location, description, "", "yitp-calendar"}); if (rule.find("FREQ=WEEKLY") == std::string::npos) break; }
    }
    return out;
}

std::vector<Event> thermal() {
    auto text = request(get("SEMINAR_WEBSITE_URL", "https://sites.google.com/view/thermalseminars")); std::vector<Event> out; std::regex dates("\\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+\\d{1,2}(?:,\\s*\\d{4})?", std::regex::icase); auto begin = std::sregex_iterator(text.begin(), text.end(), dates), end = std::sregex_iterator();
    for (auto it = begin; it != end; ++it) { auto raw = it->str(); std::smatch match; if (!std::regex_match(raw, match, std::regex("([A-Za-z]+)\\s+(\\d{1,2})(?:,\\s*(\\d{4}))?"))) continue; static const std::map<std::string, int> months{{"jan",1},{"feb",2},{"mar",3},{"apr",4},{"may",5},{"jun",6},{"jul",7},{"aug",8},{"sep",9},{"oct",10},{"nov",11},{"dec",12}}; auto month = months.find(lower(match[1].str()).substr(0, 3)); if (month == months.end()) continue; int year = match[3].matched ? std::stoi(match[3].str()) : std::stoi(today().substr(0, 4)); std::ostringstream date; date << year << '-' << std::setw(2) << std::setfill('0') << month->second << '-' << std::setw(2) << std::stoi(match[2].str()); out.push_back({date.str(), "Thermal Seminar", "", "", "11:00 AM", "102", "", "", "thermal"}); }
    return out;
}

std::vector<Event> load_cache(const std::string& path) {
    std::ifstream file(path); if (!file) return {}; std::stringstream text; text << file.rdbuf(); std::vector<Event> out;
    try { for (const auto& item : Json::parse(text.str())["events"].arr()) out.push_back({item["date"].str(), item["title"].str(), item["speaker"].str(), item["affiliation"].str(), item["time"].str(), item["location"].str(), item["description"].str(), item["link"].str(), item["source"].str()}); } catch (...) {} return out;
}
void save_cache(const std::string& path, const std::vector<Event>& events) {
    std::ofstream file(path); file << "{\"updated_at\":\"" << today() << "T00:00:00\",\"events\":["; for (size_t i = 0; i < events.size(); ++i) { if (i) file << ','; const auto& e = events[i]; file << "{\"date\":\"" << json_escape(e.date) << "\",\"title\":\"" << json_escape(e.title) << "\",\"speaker\":\"" << json_escape(e.speaker) << "\",\"affiliation\":\"" << json_escape(e.affiliation) << "\",\"time\":\"" << json_escape(e.time) << "\",\"location\":\"" << json_escape(e.location) << "\",\"description\":\"" << json_escape(e.description) << "\",\"link\":\"" << json_escape(e.link) << "\",\"source\":\"" << json_escape(e.source) << "\"}"; } file << "]}\n";
}

std::string talks(std::vector<Event> events, const std::string& heading) {
    std::sort(events.begin(), events.end()); std::ostringstream out; out << "<b>" << escape_html(heading) << "</b>\n\n"; if (events.empty()) return out.str() + "No talks found."; std::string previous;
    for (const auto& e : events) { if (e.date != previous) { if (!previous.empty()) out << "\n"; std::tm tm{}; std::istringstream(e.date) >> std::get_time(&tm, "%Y-%m-%d"); out << "<b>" << std::put_time(&tm, "%A") << ":</b>\n"; previous = e.date; } out << "• "; if (!e.title.empty()) out << "<b>" << escape_html(e.title) << "</b>"; std::vector<std::string> details; if (!e.time.empty()) details.push_back(escape_html(e.time)); if (!e.speaker.empty()) details.push_back("<i>" + escape_html(e.speaker) + "</i>"); if (!e.affiliation.empty()) details.push_back(escape_html(e.affiliation)); if (!e.location.empty()) details.push_back(escape_html(e.location)); if (!details.empty()) { out << " ("; for (size_t i = 0; i < details.size(); ++i) out << (i ? " · " : "") << details[i]; out << ")"; } if (!e.description.empty()) out << "\n  " << escape_html(e.description); out << "\n\n"; }
    return out.str();
}

class Bot {
    std::string bot_token = get("TELEGRAM_BOT_TOKEN"), cache_file = get("TALKS_CACHE_FILE", "talks-cache.json"), subscriber_file = get("SUBSCRIBERS_FILE", "subscribers.json");
    std::vector<Event> events; std::set<long long> subscribers; long long offset = 0; std::chrono::steady_clock::time_point refreshed{}; std::string announced;
    std::string api(const std::string& method, const std::string& form = "") { return request("https://api.telegram.org/bot" + bot_token + "/" + method, form); }
    std::string keyboard() const { return R"({"inline_keyboard":[[{"text":"Today","callback_data":"today"},{"text":"This week","callback_data":"week"}],[{"text":"Next week","callback_data":"nextweek"},{"text":"Lunch","callback_data":"lunch"}],[{"text":"Help","callback_data":"help"}]]})"; }
    void send(long long chat, const std::string& text) { auto form = "chat_id=" + std::to_string(chat) + "&text=" + encode(text) + "&parse_mode=HTML&reply_markup=" + encode(keyboard()); try { api("sendMessage", form); } catch (const std::exception& e) { std::cerr << "Telegram send failed: " << e.what() << '\n'; } }
    void load_subscribers() { std::ifstream file(subscriber_file); std::stringstream buffer; buffer << file.rdbuf(); auto text = buffer.str(); std::regex numbers("-?[0-9]+"); for (auto it = std::sregex_iterator(text.begin(), text.end(), numbers); it != std::sregex_iterator(); ++it) subscribers.insert(std::stoll(it->str())); }
    void save_subscribers() { std::ofstream file(subscriber_file); file << '['; bool first = true; for (auto id : subscribers) { if (!first) file << ','; file << id; first = false; } file << "]\n"; }
    std::vector<Event> range(const std::string& start, const std::string& end) const { std::vector<Event> result; for (const auto& e : events) if (e.date >= start && e.date < end) result.push_back(e); return result; }
    void refresh() {
        std::vector<Event> fetched; auto source = [&](const std::function<std::vector<Event>()>& fn) { try { auto result = fn(); fetched.insert(fetched.end(), result.begin(), result.end()); } catch (const std::exception& e) { std::cerr << "Source failed: " << e.what() << '\n'; } };
        source([&] { return yitp(get("YITP_CALENDAR_URL", "https://calendar.google.com/calendar/ical/cal%40max2.physics.sunysb.com/public/basic.ics")); }); source(thermal);
        for (const auto& id : csv(get("GOOGLE_WEDNESDAY_SPREADSHEET_IDS"))) source([&] { return sheet(id, "wednesday-seminar", "2:00 PM", "313"); });
        for (const auto& id : csv(get("GOOGLE_JOURNAL_CLUB_SPREADSHEET_IDS"))) source([&] { return sheet(id, "journal-club", "2:00 PM", "SCGP Common Room"); });
        for (const auto& id : csv(get("GOOGLE_THERMAL_SPREADSHEET_IDS"))) source([&] { return sheet(id, "thermal-seminar-sheet", "11:00 AM", "102"); });
        std::set<Event> unique; for (const auto& e : load_cache(cache_file)) if (e.date < today()) unique.insert(e); for (const auto& e : fetched) unique.insert(e); events.assign(unique.begin(), unique.end()); save_cache(cache_file, events); refreshed = std::chrono::steady_clock::now(); std::cerr << "Refreshed " << events.size() << " events\n";
    }
    void handle_command(long long chat, std::string text) {
        auto space = text.find(' '); if (space != std::string::npos) text = text.substr(0, space); text = lower(text);
        if (text == "/start") { subscribers.insert(chat); save_subscribers(); send(chat, "Subscribed to Monday announcements."); }
        else if (text == "/stop") { subscribers.erase(chat); save_subscribers(); send(chat, "Unsubscribed from announcements."); }
        else if (text == "/today") send(chat, talks(range(today(), add_days(today(), 1)), "Today"));
        else if (text == "/week") { auto start = monday(today()); send(chat, talks(range(start, add_days(start, 7)), "This week")); }
        else if (text == "/nextweek") { auto start = add_days(monday(today()), 7); send(chat, talks(range(start, add_days(start, 7)), "Next week")); }
        else if (text == "/help") send(chat, "Use /today, /week, /nextweek, or /lunch. /start subscribes to Monday announcements; /stop unsubscribes.");
        else if (text == "/lunch") { try { auto menu = request(get("LUNCH_MENU_URL", "https://www.lessings.com/my/lfsm/weekly-menu/simons-center")); menu = std::regex_replace(menu, std::regex("<[^>]*>"), " "); send(chat, "<b>Lunch menu</b>\n\n" + escape_html(menu.substr(0, 3500))); } catch (...) { send(chat, "Lunch menu is temporarily unavailable."); } }
        else if (text == "/add" || text == "/delete" || text == "/trains") send(chat, "This C++ build currently supports schedule reading and announcements. Use the Python bot for protected event editing and LIRR route planning.");
        else send(chat, "Use /today, /week, /nextweek, or /lunch.");
    }
    void callback(const Json& query) {
        try { api("answerCallbackQuery", "callback_query_id=" + encode(query["id"].str())); } catch (...) {}
        auto chat = query["message"]["chat"]["id"].number(); auto data = query["data"].str(); if (data == "today") handle_command(chat, "/today"); else if (data == "week") handle_command(chat, "/week"); else if (data == "nextweek") handle_command(chat, "/nextweek"); else if (data == "lunch") handle_command(chat, "/lunch"); else handle_command(chat, "/help");
    }
    void update(const Json& item) {
        offset = std::max(offset, static_cast<long long>(item["update_id"].number() + 1)); const auto& message = item["message"]; if (message.object()) { auto chat = message["chat"]["id"].number(); auto text = message["text"].str(); if (chat && !text.empty()) handle_command(chat, text); return; } if (item["callback_query"].object()) callback(item["callback_query"]);
    }
    void announce() {
        std::time_t now = std::time(nullptr); std::tm tm = *std::localtime(&now); if (tm.tm_wday != 1 || tm.tm_hour != 10 || tm.tm_min != 0 || announced == today()) return; auto start = monday(today()); auto text = talks(range(start, add_days(start, 7)), "This week's talks"); for (auto chat : subscribers) send(chat, text); announced = today();
    }
public:
    void run() {
        if (bot_token.empty()) throw std::runtime_error("TELEGRAM_BOT_TOKEN is missing"); load_subscribers(); refresh();
        while (true) {
            auto interval = std::chrono::seconds(std::stoll(get("REFRESH_INTERVAL_SECONDS", "3600"))); if (refreshed.time_since_epoch().count() == 0 || std::chrono::steady_clock::now() - refreshed >= interval) refresh(); announce();
            try { auto root = Json::parse(api("getUpdates", "timeout=30&offset=" + std::to_string(offset))); for (const auto& item : root["result"].arr()) update(item); }
            catch (const std::exception& e) { std::cerr << "Telegram polling failed: " << e.what() << '\n'; std::this_thread::sleep_for(std::chrono::seconds(5)); }
        }
    }
};

} // namespace

int main() {
    try { load_env(); auto timezone = get("BOT_TIMEZONE", "America/New_York"); setenv("TZ", timezone.c_str(), 1); tzset(); curl_global_init(CURL_GLOBAL_DEFAULT); Bot bot; bot.run(); curl_global_cleanup(); }
    catch (const std::exception& error) { std::cerr << "Fatal: " << error.what() << '\n'; return 1; }
    return 0;
}
