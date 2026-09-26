import base64
import datetime
import io
import json
import requests
import PIL.Image
import streamlit as st

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

# ---------------------------------------------------------
# 1. PAGE CONFIGURATION & CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title="Event Planner",
    page_icon="📅",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# Responsive Design für Smartphones
st.markdown("""
    <style>
    .stApp {
        max-width: 800px;
        margin: 0 auto;
    }
    .event-card {
        background-color: #f8f9fa;
        padding: 15px;
        border-radius: 12px;
        border: 1px solid #e9ecef;
        margin-bottom: 15px;
    }
    .stButton button {
        width: 100%;
        border-radius: 8px;
    }
    </style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------
# 2. GOOGLE DRIVE PERSISTENCE (SERVICE ACCOUNT)
# ---------------------------------------------------------
def get_drive_service():
    """Authentifiziert sich über den Service-Account aus den Secrets bei Google Drive."""
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build('drive', 'v3', credentials=creds)


def load_data():
    """Lädt die data.json aus dem festgelegten Google-Drive-Ordner."""
    try:
        service = get_drive_service()
        folder_id = st.secrets["DRIVE_FOLDER_ID"]

        query = f"'{folder_id}' in parents and name = 'data.json' and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])

        if not files:
            # Standard-Startdaten, falls noch keine data.json existiert
            return {"users": ["Anna", "Julian", "Matthias"], "events": []}

        file_id = files[0]['id']
        request = service.files().get_media(fileId=file_id)
        file_stream = io.BytesIO()
        downloader = MediaIoBaseDownload(file_stream, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        file_stream.seek(0)
        return json.loads(file_stream.read().decode('utf-8'))
    except Exception as e:
        st.error(f"Fehler beim Laden von Google Drive: {e}")
        return {"users": ["Anna", "Julian", "Matthias"], "events": []}


def save_data(data):
    """Speichert die data.json direkt im Google-Drive-Ordner."""
    try:
        service = get_drive_service()
        folder_id = st.secrets["DRIVE_FOLDER_ID"]

        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        media = MediaInMemoryUpload(json_bytes, mimetype='application/json', resumable=True)

        query = f"'{folder_id}' in parents and name = 'data.json' and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])

        if files:
            # Existierende Datei überschreiben
            file_id = files[0]['id']
            service.files().update(fileId=file_id, media_body=media).execute()
        else:
            # Neue Datei anlegen
            file_metadata = {'name': 'data.json', 'parents': [folder_id]}
            service.files().create(body=file_metadata, media_body=media).execute()

        return True
    except Exception as e:
        st.error(f"Fehler beim Speichern in Google Drive: {e}")
        return False


# ---------------------------------------------------------
# 3. HELPER FUNCTIONS (IMAGE & AI FLYER SCAN)
# ---------------------------------------------------------
def image_to_base64(image):
    """Konvertiert ein PIL Image in einen Base64-String."""
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def analyze_flyer_with_gemini(image_bytes):
    """Analysiert einen Event-Flyer per Gemini API und gibt strukturierte Event-Daten zurück."""
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        st.error("Kein GEMINI_API_KEY in den Secrets konfiguriert.")
        return None

    try:
        b64_image = base64.b64encode(image_bytes).decode('utf-8')
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"

        prompt = """
        Analysiere diesen Event-Flyer und extrahiere die folgenden Informationen als JSON:
        {
          "title": "Titel des Events",
          "date": "YYYY-MM-DD",
          "time": "HH:MM",
          "location": "Ort / Location",
          "description": "Kurze Event-Beschreibung"
        }
        Falls ein Feld nicht auf dem Flyer steht, gib als Wert einen leeren String "" an.
        Antworte AUSSCHLIESSLICH im reinen JSON-Format ohne Markdown-Codeblöcke.
        """

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}}
                ]
            }]
        }

        res = requests.post(url, json=payload, headers={"Content-Type": "application/json"})
        if res.status_code == 200:
            text = res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        else:
            st.error(f"Gemini API Fehler ({res.status_code}): {res.text}")
            return None
    except Exception as e:
        st.error(f"Fehler bei der KI-Analyse: {e}")
        return None


# ---------------------------------------------------------
# 4. SESSION STATE INITIALIZATION
# ---------------------------------------------------------
if "data" not in st.session_state:
    st.session_state["data"] = load_data()

data = st.session_state["data"]


# ---------------------------------------------------------
# 5. SIDEBAR / HEADER: USER SELECTION
# ---------------------------------------------------------
st.title("📅 Event Planner")

with st.sidebar:
    st.header("👤 Profil wählen")
    current_user = st.selectbox("Wer bist du?", data.get("users", ["Gast"]))
    st.session_state["current_user"] = current_user

    st.divider()
    if st.button("🔄 Daten neu aus Drive laden"):
        st.session_state["data"] = load_data()
        st.rerun()

st.caption(f"Angemeldet als: **{st.session_state.get('current_user', 'Gast')}**")


# ---------------------------------------------------------
# 6. NAVIGATION TABS
# ---------------------------------------------------------
tab_events, tab_add, tab_admin = st.tabs(["📅 Übersicht", "➕ Neues Event", "⚙️ Admin"])


# --- TAB 1: EVENT ÜBERSICHT ---
with tab_events:
    events = data.get("events", [])

    if not events:
        st.info("Noch keine Events vorhanden. Erstelle eines im Tab 'Neues Event'!")
    else:
        # Sortieren nach Datum
        events_sorted = sorted(events, key=lambda x: (x.get("date", ""), x.get("time", "")))

        for idx, event in enumerate(events_sorted):
            with st.container():
                st.markdown(f"### {event.get('title', 'Unbenanntes Event')}")

                col_img, col_info = st.columns([1, 2]) if event.get("flyer_b64") else (None, st.container())

                if event.get("flyer_b64"):
                    with col_img:
                        try:
                            img_data = base64.b64decode(event["flyer_b64"])
                            st.image(img_data, use_column_width=True)
                        except Exception:
                            st.caption("Bild konnte nicht geladen werden")

                with col_info:
                    st.write(f"📆 **Datum:** {event.get('date', 'N/A')}")
                    st.write(f"⏰ **Uhrzeit:** {event.get('time', 'N/A')}")
                    st.write(f"📍 **Ort:** {event.get('location', 'N/A')}")
                    if event.get("description"):
                        st.write(f"💬 {event.get('description')}")
                    st.caption(f"Erstellt von: {event.get('created_by', 'Anonym')}")

                # Teilnahme verwalten
                participants = event.get("participants", [])
                st.write(f"👥 **Teilnehmer ({len(participants)}):** {', '.join(participants) if participants else 'Noch niemand'}")

                current_user = st.session_state.get("current_user")
                if current_user:
                    is_attending = current_user in participants
                    btn_label = "❌ Absagen" if is_attending else "✅ Zusage erteilen"

                    if st.button(btn_label, key=f"join_{event.get('id', idx)}"):
                        if is_attending:
                            participants.remove(current_user)
                        else:
                            participants.append(current_user)

                        event["participants"] = participants
                        if save_data(data):
                            st.success("Teilnahme aktualisiert!")
                            st.rerun()

                st.divider()


# --- TAB 2: NEUES EVENT ERSTELLEN ---
with tab_add:
    st.subheader("Event hinzufügen")

    # Option 1: AI Flyer Scan
    with st.expander("✨ Flyer hochladen & mit KI ausfüllen", expanded=False):
        uploaded_flyer = st.file_uploader("Flyer-Bild auswählen", type=["jpg", "jpeg", "png"], key="flyer_scanner")
        if uploaded_flyer and st.button("🪄 Flyer mit KI analysieren"):
            with st.spinner("Gemini analysiert das Flyer-Bild..."):
                file_bytes = uploaded_flyer.read()
                ai_data = analyze_flyer_with_gemini(file_bytes)
                if ai_data:
                    st.session_state["form_title"] = ai_data.get("title", "")
                    st.session_state["form_date"] = ai_data.get("date", "")
                    st.session_state["form_time"] = ai_data.get("time", "")
                    st.session_state["form_location"] = ai_data.get("location", "")
                    st.session_state["form_desc"] = ai_data.get("description", "")

                    # Bild direkt im Session-State speichern
                    img = PIL.Image.open(io.BytesIO(file_bytes))
                    st.session_state["form_b64"] = image_to_base64(img)

                    st.success("Daten erfolgreich aus dem Flyer extrahiert!")
                    st.rerun()

    # Formular
    with st.form("event_form", clear_on_submit=True):
        title = st.text_input("Titel*", value=st.session_state.get("form_title", ""))

        col_d, col_t = st.columns(2)
        with col_d:
            # Datumsfeld mit Standardwert
            default_date = datetime.date.today()
            if st.session_state.get("form_date"):
                try:
                    default_date = datetime.datetime.strptime(st.session_state["form_date"], "%Y-%m-%d").date()
                except ValueError:
                    pass
            event_date = st.date_input("Datum*", value=default_date)

        with col_t:
            event_time = st.text_input("Uhrzeit (z. B. 19:30)", value=st.session_state.get("form_time", "19:00"))

        location = st.text_input("Ort / Location", value=st.session_state.get("form_location", ""))
        description = st.text_area("Beschreibung", value=st.session_state.get("form_desc", ""))

        manual_flyer = st.file_uploader("Flyer-Bild anhängen (optional)", type=["jpg", "jpeg", "png"], key="manual_flyer")

        submit_btn = st.form_submit_button("💾 Event speichern")

        if submit_btn:
            if not title:
                st.error("Bitte gib einen Titel für das Event ein.")
            else:
                # Bild verarbeiten
                b64_img = st.session_state.get("form_b64", "")
                if manual_flyer:
                    img = PIL.Image.open(manual_flyer)
                    b64_img = image_to_base64(img)

                new_event = {
                    "id": str(datetime.datetime.now().timestamp()),
                    "title": title,
                    "date": str(event_date),
                    "time": event_time,
                    "location": location,
                    "description": description,
                    "created_by": st.session_state.get("current_user", "Anonym"),
                    "participants": [st.session_state.get("current_user", "Anonym")],
                    "flyer_b64": b64_img
                }

                data["events"].append(new_event)

                if save_data(data):
                    st.success("Event erfolgreich in Google Drive gespeichert!")
                    # Temp-Formulardaten aufräumen
                    for key in ["form_title", "form_date", "form_time", "form_location", "form_desc", "form_b64"]:
                        st.session_state.pop(key, None)
                    st.rerun()


# --- TAB 3: ADMIN BEREICH ---
with tab_admin:
    st.subheader("⚙️ Admin-Bereich")

    admin_pin = st.text_input("Admin PIN eingeben", type="password")
    expected_pin = st.secrets.get("ADMIN_PIN", "#together#")

    if admin_pin == expected_pin:
        st.success("Admin-Zugriff gestattet.")

        st.markdown("---")
        st.write("### 👥 Nutzer verwalten")
        new_user_name = st.text_input("Neuen Nutzer hinzufügen")
        if st.button("Nutzer anlegen"):
            if new_user_name and new_user_name not in data["users"]:
                data["users"].append(new_user_name)
                save_data(data)
                st.success(f"Nutzer '{new_user_name}' hinzugefügt.")
                st.rerun()

        st.markdown("---")
        st.write("### 🗑️ Events verwalten / löschen")
        for event in data.get("events", []):
            col_t, col_b = st.columns([3, 1])
            with col_t:
                st.write(f"**{event.get('title')}** ({event.get('date')})")
            with col_b:
                if st.button("Löschen", key=f"del_{event.get('id')}"):
                    data["events"] = [e for e in data["events"] if e.get("id") != event.get("id")]
                    save_data(data)
                    st.success("Event gelöscht.")
                    st.rerun()

    elif admin_pin != "":
        st.error("Falscher PIN!")