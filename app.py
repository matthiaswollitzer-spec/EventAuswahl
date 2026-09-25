import base64
import io
import json
import os
import time
from google import genai
from google.genai import types
from google.genai.errors import APIError
from PIL import Image
import streamlit as st

# File-Pfad für die zentrale Datenspeicherung
DATA_FILE = "events.json"

st.set_page_config(
    page_title="WhatsApp Event Planner", page_icon="🎉", layout="wide"
)

# ---------------------------------------------------------
# Hilfsfunktionen für zentrale Speicherung
# ---------------------------------------------------------
def load_events():
    """Lädt alle Events aus der zentralen JSON-Datei."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_events(events):
    """Speichert die Events-Liste in der zentralen JSON-Datei."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

def image_to_base64(image):
    """Konvertiert ein PIL-Bild in einen Base64-String (wandelt RGBA vorher in RGB um)."""
    buffered = io.BytesIO()
    
    # Transparente Kanäle (PNG/RGBA) in RGB umwandeln für JPEG-Export
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
        
    image.thumbnail((400, 400))  # Bild zur Performance-Optimierung verkleinern
    image.save(buffered, format="JPEG", quality=80)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

# ---------------------------------------------------------
# Secrets & Benutzer-Identifikation via URL (st.query_params)
# ---------------------------------------------------------
try:
    api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    api_key = None
    st.error("Fehler: Kein 'GEMINI_API_KEY' in den Streamlit Secrets hinterlegt!")

current_user = st.query_params.get("user", "")

if "edit_name" not in st.session_state:
    st.session_state.edit_name = False

# Name abfragen oder ändern
if not current_user or st.session_state.edit_name:
    st.title("🎉 Event Planner")
    st.warning("👋 Bitte gib deinen Namen für die Abstimmung ein.")
    input_name = st.text_input("Dein Name (z. B. Alex):", value=current_user)
    
    if st.button("Name speichern"):
        if input_name.strip():
            st.query_params["user"] = input_name.strip()
            st.session_state.edit_name = False
            st.rerun()
        else:
            st.error("Bitte gib einen gültigen Namen ein.")
    st.stop()

# Header & Namensanzeige mit Änderungs-Button
st.title("🎉 Event Planner: Flyer-Analyse & Abstimmung")

col_u1, col_u2 = st.columns([3, 1])
with col_u1:
    st.caption(f"Eingeloggt als: **{current_user}**")
with col_u2:
    if st.button("✏️ Name ändern", key="btn_change_name"):
        st.session_state.edit_name = True
        st.rerun()

# ---------------------------------------------------------
# KI-Analyse mit Gemini 3.8 Flash
# ---------------------------------------------------------
def analyze_flyer(image, key, max_retries=3):
    client = genai.Client(api_key=key)
    prompt = """
    Analysiere diesen Flyer/Screenshot für ein Event oder eine Party. 
    Extrahiere folgende Informationen im exakten JSON-Format:
    {
        "title": "Name des Events",
        "date": "Datum oder Wochentag",
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

# Session State für Zwischenspeicherung (Schritt 1 -> Schritt 2)
if "pending_event" not in st.session_state:
    st.session_state.pending_event = None

# ---------------------------------------------------------
# Bereich 1: Uploader & 2-Stufen Analyse
# ---------------------------------------------------------
st.header("1. Neuer Flyer hochladen")

uploaded_file = st.file_uploader(
    "Bild auswählen (PNG, JPG)", type=["png", "jpg", "jpeg"]
)

if uploaded_file and api_key:
    image = Image.open(uploaded_file)
    st.image(image, caption="Vorschau Upload", width=200)

    # Schritt 1: Analyse ausführen
    if st.button("🔍 1. Flyer analysieren"):
        with st.spinner("Analysiere Bild mit Gemini API..."):
            try:
                extracted_data = analyze_flyer(image, api_key)
                extracted_data["image_base64"] = image_to_base64(image)
                st.session_state.pending_event = extracted_data
                st.success("Analyse erfolgreich! Überprüfe die Daten unten.")
                st.rerun()
            except APIError as e:
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    st.warning(
                        "⚠️ Das kostenlose Anfragen-Limit der Gemini API ist vorübergehend erreicht. "
                        "Bitte warte 15–30 Sekunden und klicke dann erneut auf 'Flyer analysieren'."
                    )
                elif "503" in err_msg or "UNAVAILABLE" in err_msg:
                    st.warning(
                        "⚠️ Die KI ist gerade sehr stark ausgelastet (Fehler 503). "
                        "Bitte warte etwa 10–20 Sekunden und versuche es erneut."
                    )
                else:
                    st.error(f"API-Fehler bei der Analyse: {e}")
            except Exception as e:
                st.error(f"Fehler bei der Analyse: {e}")

# Schritt 2: Korrektur-Formular (falls Analyse vorliegt)
if st.session_state.pending_event:
    st.subheader("📋 Daten überprüfen & bei Bedarf anpassen")
    pending = st.session_state.pending_event

    col_form_img, col_form_inputs = st.columns([1, 2])

    with col_form_img:
        if "image_base64" in pending:
            img_bytes = base64.b64decode(pending["image_base64"])
            st.image(img_bytes, caption="Analysierter Flyer", width=220)

    with col_form_inputs:
        edited_title = st.text_input("Titel des Events", value=pending.get("title", ""))
        col_d, col_t = st.columns(2)
        with col_d:
            edited_date = st.text_input("Datum", value=pending.get("date", ""))
        with col_t:
            edited_time = st.text_input("Uhrzeit", value=pending.get("time", ""))
        
        edited_location = st.text_input("Ort / Location", value=pending.get("location", ""))
        edited_description = st.text_area("Beschreibung", value=pending.get("description", ""))

        btn_col1, btn_col2 = st.columns([1, 1])
        with btn_col1:
            if st.button("🚀 2. Event veröffentlichen", type="primary"):
                final_event = {
                    "title": edited_title,
                    "date": edited_date,
                    "time": edited_time,
                    "location": edited_location,
                    "description": edited_description,
                    "votes": 0,
                    "voters": [],
                    "image_base64": pending.get("image_base64", "")
                }
                
                events = load_events()
                events.append(final_event)
                save_events(events)

                st.session_state.pending_event = None
                st.success(f"Event '{edited_title}' veröffentlicht!")
                st.rerun()

        with btn_col2:
            if st.button("❌ Abbrechen"):
                st.session_state.pending_event = None
                st.rerun()

st.divider()

# ---------------------------------------------------------
# Bereich 2: Event-Übersicht & Abstimmung (Live)
# ---------------------------------------------------------
st.header("2. Aktuelle Abstimmung")

events = load_events()

if not events:
    st.info("Noch keine Events vorhanden. Lade oben den ersten Flyer hoch!")
else:
    for idx, event in enumerate(events):
        col1, col2 = st.columns([1, 3])

        with col1:
            if "image_base64" in event and event["image_base64"]:
                img_bytes = base64.b64decode(event["image_base64"])
                st.image(img_bytes, width=200)

        with col2:
            st.subheader(event.get("title", "Unbekanntes Event"))
            st.write(
                f"📅 **Datum:** {event.get('date', 'N/A')} | ⏰ **Uhrzeit:** {event.get('time', 'N/A')}"
            )
            st.write(f"📍 **Ort:** {event.get('location', 'N/A')}")
            st.write(f"📝 {event.get('description', '')}")

            has_voted = current_user in event.get("voters", [])
            button_label = (
                "❌ Stimme zurückziehen"
                if has_voted
                else f"👍 Dafür stimmen ({event.get('votes', 0)})"
            )

            if st.button(button_label, key=f"vote_{idx}"):
                if not has_voted:
                    event["votes"] += 1
                    event["voters"].append(current_user)
                else:
                    event["votes"] -= 1
                    event["voters"].remove(current_user)

                save_events(events)
                st.rerun()

            if event.get("voters"):
                st.caption(f"Stimmen von: {', '.join(event['voters'])}")

        st.divider()