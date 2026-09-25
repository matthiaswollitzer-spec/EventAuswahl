import base64
from datetime import datetime, timedelta
import io
import json
import os
import time
import streamlit as st
import streamlit.components.v1 as components
from google import genai
from google.genai import types
from google.genai.errors import APIError
from PIL import Image

# Pfad für die zentrale Datenspeicherung
DATA_FILE = "events.json"

st.set_page_config(
    page_title="WhatsApp Event Planner", page_icon="🎉", layout="wide"
)

# ---------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------
def format_german_date(date_iso_str):
    """Konvertiert YYYY-MM-DD in 'Wochentag, Tag. Monat Jahr' (z. B. Samstag, 26. September 2026)."""
    try:
        dt = datetime.strptime(date_iso_str, "%Y-%m-%d")
        weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        return f"{weekdays[dt.weekday()]}, {dt.day}. {months[dt.month - 1]} {dt.year}"
    except Exception:
        return date_iso_str

def load_data():
    """Lädt Events und User-Dictionary (Name -> Passwort) aus der JSON-Datei."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "users" not in data or not isinstance(data["users"], dict):
                    data["users"] = {}
                if "events" not in data:
                    data["events"] = []
                return data
        except Exception:
            return {"users": {}, "events": []}
    return {"users": {}, "events": []}

def save_data(data):
    """Speichert Daten in die JSON-Datei."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def delete_user_completely(data, target_username):
    """Löscht einen User spurlos: Aus dem Register & aus allen Abstimmungen."""
    if target_username in data.get("users", {}):
        del data["users"][target_username]
    
    for event in data.get("events", []):
        if target_username in event.get("voters", []):
            event["voters"].remove(target_username)
            event["votes"] = len(event["voters"])
            
    save_data(data)

def image_to_base64(image):
    buffered = io.BytesIO()
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    image.thumbnail((400, 400))
    image.save(buffered, format="JPEG", quality=80)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

# ---------------------------------------------------------
# Secrets & Authentifizierung
# ---------------------------------------------------------
try:
    api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    api_key = None
    st.error("Fehler: Kein 'GEMINI_API_KEY' in den Streamlit Secrets hinterlegt!")

ADMIN_PIN = st.secrets.get("ADMIN_PIN", "1234")
data = load_data()

# ---------------------------------------------------------
# Session State & Login-Verwaltung (Name & Passwort)
# ---------------------------------------------------------
if "logged_in_user" not in st.session_state:
    st.session_state.logged_in_user = None

if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0

# Entwickler-Komfort für den lokalen Debugger (automatischer Login)
if not st.session_state.logged_in_user and "DEV_USER_NAME" in st.secrets:
    dev_name = st.secrets.get("DEV_USER_NAME", "Matthias")
    dev_pass = st.secrets.get("DEV_USER_PASS", "1234")
    if dev_name not in data["users"]:
        data["users"][dev_name] = dev_pass
        save_data(data)
    st.session_state.logged_in_user = dev_name

# Wenn nicht eingeloggt: Login-Bereich anzeigen
if not st.session_state.logged_in_user:
    st.title("🎉 Event Planner - Login")
    st.warning("👋 Bitte melde dich an oder registriere dich.")
    st.info("💡 **Tipp fürs Handy:** Falls der Passwort-Manager in WhatsApp nicht greift, öffne den Link über die drei Punkte im **echten Browser** (Chrome / Safari).")

    components.html("""
        <form style="opacity: 0; height: 0px; overflow: hidden;">
            <input type="text" name="username" autocomplete="username">
            <input type="password" name="password" autocomplete="current-password">
        </form>
    """, height=0)

    with st.form("login_form"):
        input_name = st.text_input("Name (Benutzer)", placeholder="z. B. Matthias")
        input_pass = st.text_input("Passwort", type="password", placeholder="Dein Passwort")
        
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            submit_login = st.form_submit_button("Anmelden", type="primary", use_container_width=True)
        with col_f2:
            submit_register = st.form_submit_button("Neu registrieren", use_container_width=True)

        if submit_login:
            name_clean = input_name.strip()
            users_db = data.get("users", {})
            if not name_clean or not input_pass:
                st.error("Bitte Name und Passwort eingeben.")
            elif name_clean in users_db and users_db[name_clean] == input_pass:
                st.session_state.logged_in_user = name_clean
                st.success("Erfolgreich angemeldet!")
                st.rerun()
            else:
                st.error("Falscher Name oder falsches Passwort.")

        if submit_register:
            name_clean = input_name.strip()
            users_db = data.get("users", {})
            if not name_clean:
                st.error("Bitte gib einen Namen ein.")
            elif not input_pass:
                st.error("Bitte gib ein Passwort ein.")
            elif name_clean in users_db:
                st.error("Dieser Name ist bereits vergeben. Bitte wähle einen anderen.")
            else:
                users_db[name_clean] = input_pass
                data["users"] = users_db
                save_data(data)
                st.session_state.logged_in_user = name_clean
                st.success("Erfolgreich registriert und eingeloggt!")
                st.rerun()
    st.stop()

current_user_name = st.session_state.logged_in_user

# ---------------------------------------------------------
# Sidebar / Admin-Modus (User- & Archiv-Verwaltung)
# ---------------------------------------------------------
st.sidebar.title("👤 Profil & Admin")
st.sidebar.write(f"Eingeloggt als: **{current_user_name}**")

if st.sidebar.button("🚪 Abmelden"):
    st.session_state.logged_in_user = None
    st.rerun()

st.sidebar.divider()
st.sidebar.subheader("🔒 Superuser / Admin")
admin_pin_input = st.sidebar.text_input("Admin-PIN eingeben", type="password")
is_admin = admin_pin_input == ADMIN_PIN

if is_admin:
    st.sidebar.success("🔑 Admin-Modus aktiv")

    # 1. User-Verwaltung
    with st.sidebar.expander("👥 User verwalten", expanded=True):
        users_dict = data.get("users", {})
        if not users_dict:
            st.info("Keine User registriert.")
        else:
            for uname in list(users_dict.keys()):
                ucol1, ucol2 = st.columns([3, 1])
                ucol1.write(f"• **{uname}**")
                if ucol2.button("🗑️", key=f"del_user_{uname}"):
                    delete_user_completely(data, uname)
                    st.sidebar.success(f"User '{uname}' & Stimmen gelöscht!")
                    st.rerun()

    # 2. Massenlöschung alter Events
    with st.sidebar.expander("🧹 Archiv aufräumen", expanded=False):
        cutoff_date = st.date_input(
            "Lösche Events vor Datum:",
            value=datetime.now().date() - timedelta(days=30),
        )
        if st.button("🗑️ Alte Events löschen", type="primary"):
            cutoff_str = cutoff_date.strftime("%Y-%m-%d")
            before_count = len(data["events"])
            
            data["events"] = [
                e for e in data["events"]
                if e.get("date_iso", "9999-99-99") >= cutoff_str
            ]
            removed = before_count - len(data["events"])
            save_data(data)
            st.sidebar.success(f"{removed} alte(s) Event(s) gelöscht!")
            st.rerun()

elif admin_pin_input:
    st.sidebar.error("Falsche PIN")

st.title("🎉 Event Planner")

# ---------------------------------------------------------
# KI-Analyse mit Gemini 3.8 Flash (Flyer & Screenshots)
# ---------------------------------------------------------
def analyze_flyer(image, key, max_retries=3):
    client = genai.Client(api_key=key)
    prompt = """
    Analysiere diesen Flyer oder Screenshot (der auch mehrere Events enthalten kann, konzentriere dich auf das prominenteste oder erste Event). 
    Extrahiere folgende Informationen im exakten JSON-Format:
    {
        "title": "Name des Events",
        "date_iso": "Datum im Format YYYY-MM-DD (falls Jahr fehlt, nimm 2026)",
        "date_display": "Lesbares Datum (z. B. Samstag, 15. Oktober)",
        "time": "Uhrzeit/Beginn",
        "location": "Ort/Club/Adresse",
        "description": "Kurze Zusammenfassung in 1 Satz"
    }
    """
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[image, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                ),
            )
            return json.loads(response.text)
        except APIError as e:
            if attempt < max_retries:
                time.sleep(attempt * 2)
            else:
                raise e
        except Exception as e:
            raise e

if "pending_events" not in st.session_state:
    st.session_state.pending_events = []

# ---------------------------------------------------------
# Bereich 1: Flyer / Screenshots hochladen (Multi-Upload fähig)
# ---------------------------------------------------------
st.header("1. Flyer oder Screenshots hinzufügen")
st.info("💡 **Tipp fürs Handy:** Du kannst in deiner Galerie (unter 'Screenshots') auch **mehrere Screenshots** gleichzeitig markieren und hochladen!")

uploaded_files = st.file_uploader(
    "Bilder/Screenshots auswählen (PNG, JPG)", 
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=True,
    key=f"uploader_{st.session_state.uploader_key}"
)

if uploaded_files and api_key and not st.session_state.pending_events:
    if st.button(f"🔍 {len(uploaded_files)} Bild(er) analysieren"):
        with st.spinner("Analysiere Bilder mit Gemini API..."):
            try:
                analyzed_list = []
                for upl_file in uploaded_files:
                    image = Image.open(upl_file)
                    extracted_data = analyze_flyer(image, api_key)
                    extracted_data["image_base64"] = image_to_base64(image)
                    analyzed_list.append(extracted_data)
                
                st.session_state.pending_events = analyzed_list
                st.success(f"{len(analyzed_list)} Bild(er) erfolgreich analysiert! Prüfe die Daten unten.")
                st.rerun()
            except Exception as e:
                st.error(f"Fehler bei der Analyse: {e}")

if st.session_state.pending_events:
    st.subheader(f"📋 Daten überprüfen & veröffentlichen ({len(st.session_state.pending_events)} ausstehend)")

    # Wir gehen die Liste der analysierten Events durch
    for i, pending in enumerate(list(st.session_state.pending_events)):
        with st.expander(f"Event {i+1}: {pending.get('title', 'Neues Event')} ({pending.get('date_display', '')})", expanded=(i==0)):
            col_form_img, col_form_inputs = st.columns([1, 2])
            with col_form_img:
                if "image_base64" in pending:
                    st.image(
                        base64.b64decode(pending["image_base64"]),
                        caption="Analysierter Screenshot/Flyer",
                        width=200,
                    )

            with col_form_inputs:
                edited_title = st.text_input("Titel des Events", value=pending.get("title", ""), key=f"title_{i}")
                col_d1, col_d2, col_t = st.columns([1, 1, 1])
                with col_d1:
                    edited_date_display = st.text_input("Datum (Anzeige)", value=pending.get("date_display", ""), key=f"dd_{i}")
                with col_d2:
                    edited_date_iso = st.text_input(
                        "Datum (YYYY-MM-DD)",
                        value=pending.get("date_iso", datetime.now().strftime("%Y-%m-%d")),
                        key=f"di_{i}"
                    )
                with col_t:
                    edited_time = st.text_input("Uhrzeit", value=pending.get("time", ""), key=f"time_{i}")

                edited_location = st.text_input("Ort / Location", value=pending.get("location", ""), key=f"loc_{i}")
                edited_description = st.text_area("Beschreibung", value=pending.get("description", ""), key=f"desc_{i}")

                b_col1, b_col2 = st.columns([1, 1])
                with b_col1:
                    if st.button("🚀 Dieses Event veröffentlichen", key=f"pub_{i}", type="primary"):
                        final_event = {
                            "id": str(int(time.time())) + f"_{i}",
                            "title": edited_title,
                            "date_display": edited_date_display,
                            "date_iso": edited_date_iso,
                            "time": edited_time,
                            "location": edited_location,
                            "description": edited_description,
                            "votes": 0,
                            "voters": [],
                            "image_base64": pending.get("image_base64", ""),
                        }
                        data["events"].append(final_event)
                        save_data(data)
                        # Aus der Pending-Liste entfernen
                        st.session_state.pending_events.pop(i)
                        if not st.session_state.pending_events:
                            st.session_state.uploader_key += 1
                        st.success(f"Event '{edited_title}' veröffentlicht!")
                        st.rerun()
                with b_col2:
                    if st.button("❌ Verwerfen", key=f"drop_{i}"):
                        st.session_state.pending_events.pop(i)
                        if not st.session_state.pending_events:
                            st.session_state.uploader_key += 1
                        st.rerun()

    if st.button("🗑️ Alle ausstehenden verwerfen"):
        st.session_state.pending_events = []
        st.session_state.uploader_key += 1
        st.rerun()

st.divider()

# ---------------------------------------------------------
# Bereich 2: Event-Übersicht (Kompakt & Einklappbare Beschreibung)
# ---------------------------------------------------------
st.header("2. Event-Übersicht & Abstimmung")

events = data.get("events", [])
today = datetime.now().date()
end_of_week = today + timedelta(days=7)

current_week_events = []
future_events = []
past_events = []

seen_ids = set()

for ev in events:
    ev_id = ev.get("id")
    if ev_id in seen_ids:
        continue
    seen_ids.add(ev_id)

    raw_date = ev.get("date_iso", "")
    try:
        event_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        event_date = datetime(2099, 12, 31).date()

    if event_date < today:
        past_events.append(ev)
    elif today <= event_date <= end_of_week:
        current_week_events.append(ev)
    else:
        future_events.append(ev)

tab_current, tab_future, tab_past = st.tabs([
    f"🔥 Diese Woche ({len(current_week_events)})",
    f"🔮 Demnächst / Zukunft ({len(future_events)})",
    f"📦 Archiv / Vergangen ({len(past_events)})",
])

def render_event_list(event_list, is_past=False):
    if not event_list:
        st.info("Keine Events in dieser Kategorie.")
        return

    sorted_events = sorted(
        event_list,
        key=lambda x: (x.get("date_iso", "9999-99-99"), x.get("time", ""))
    )

    last_date = None

    for idx, event in enumerate(sorted_events):
        event_date_iso = event.get("date_iso", "")

        if event_date_iso != last_date:
            formatted_day = format_german_date(event_date_iso)
            st.markdown(f"#### 📅 {formatted_day}")
            st.divider()
            last_date = event_date_iso

        event_id = event.get("id", str(idx))
        col1, col2 = st.columns([1, 3])

        voters_list = event.get("voters", [])

        with col1:
            if event.get("image_base64"):
                st.image(base64.b64decode(event["image_base64"]), width=200)

            if is_past:
                st.info(f"🏆 **Endergebnis:** {len(voters_list)} Stimmen")
            else:
                has_voted = current_user_name in voters_list
                button_label = (
                    "❌ Stimme zurückziehen"
                    if has_voted
                    else f"👍 Dafür stimmen ({len(voters_list)})"
                )

                if st.button(button_label, key=f"vote_{event_id}", use_container_width=True):
                    if not has_voted:
                        voters_list.append(current_user_name)
                    else:
                        voters_list.remove(current_user_name)
                    event["votes"] = len(voters_list)
                    save_data(data)
                    st.rerun()

        with col2:
            st.subheader(event.get("title", "Unbekanntes Event"))
            st.write(
                f"📅 **Datum:** {event.get('date_display', event.get('date_iso', 'N/A'))} | ⏰ **Uhrzeit:** {event.get('time', 'N/A')}"
            )
            st.write(f"📍 **Ort:** {event.get('location', 'N/A')}")
            
            desc = event.get("description", "")
            if desc:
                with st.expander("📝 Beschreibung anzeigen", expanded=False):
                    st.markdown(f"<small>{desc}</small>", unsafe_allow_html=True)

            if voters_list:
                st.caption(f"Stimmen von: {', '.join(voters_list)}")

            if is_admin:
                st.markdown("---")
                st.caption("🛠️ **Admin-Aktionen:**")
                adm_col1, adm_col2 = st.columns([1, 1])

                with adm_col1:
                    if st.button("🗑️ Event löschen", key=f"del_{event_id}"):
                        data["events"] = [e for e in data["events"] if e.get("id") != event_id]
                        save_data(data)
                        st.success("Event gelöscht!")
                        st.rerun()

                with adm_col2:
                    with st.popover("✏️ Event anpassen"):
                        new_title = st.text_input("Titel", value=event.get("title"), key=f"ed_t_{event_id}")
                        new_date_disp = st.text_input("Datum (Anzeige)", value=event.get("date_display"), key=f"ed_d_{event_id}")
                        new_date_iso = st.text_input("Datum (YYYY-MM-DD)", value=event.get("date_iso"), key=f"ed_iso_{event_id}")
                        new_loc = st.text_input("Ort", value=event.get("location"), key=f"ed_l_{event_id}")

                        if st.button("Änderungen speichern", key=f"save_ed_{event_id}"):
                            event["title"] = new_title
                            event["date_display"] = new_date_disp
                            event["date_iso"] = new_date_iso
                            event["location"] = new_loc
                            save_data(data)
                            st.success("Gespeichert!")
                            st.rerun()

        st.write("")

with tab_current:
    render_event_list(current_week_events, is_past=False)

with tab_future:
    render_event_list(future_events, is_past=False)

with tab_past:
    render_event_list(past_events, is_past=True)