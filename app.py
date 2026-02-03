import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime
import math
import io
import json
from fpdf import FPDF # Libreria per creare il PDF

# --- CONFIGURAZIONE ---
st.set_page_config(page_title="Gestione Magazzino", layout="wide", initial_sidebar_state="expanded")

MESI_COPERTURA = 1.0      
MESI_BUFFER = 0.5         
TARGET_MESI = MESI_COPERTURA + MESI_BUFFER 
MIN_SCORTA_CAL = 3        

# --- CONNESSIONE GOOGLE SHEETS ---
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("⚠️ Errore Segreti: Configura .streamlit/secrets.toml")
    st.stop()

# --- CARICAMENTO DATI ---
@st.cache_data
def load_master_data():
    try:
        df = pd.read_excel('dati.xlsx', engine='openpyxl')
        
        if 'LN ABBOTT' in df.columns and 'LN ABBOTT AGGIORNATI' in df.columns:
            df['Codice_Finale'] = df['LN ABBOTT'].fillna(df['LN ABBOTT AGGIORNATI'])
        else:
            df['Codice_Finale'] = df.iloc[:, 4] 

        col_map = {
            'Codice_Finale': 'Codice',
            'Descrizione commerciale': 'Descrizione',
            'Rgt/Cal/QC/Cons': 'Categoria',
            '# Kit/Mese': 'Fabbisogno_Kit_Mese_Stimato', 
            'Test TOT MEDI/MESE Aggiustati': 'Test_Mensili_Reali',
            'KIT': 'Test_per_Scatola',
            'Conf.to': 'Confezione'
        }
        
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        df = df[df['Descrizione'].notna() & df['Codice'].notna()] 
        df['Codice'] = df['Codice'].astype(str).str.replace('.0', '', regex=False)
        
        for col in ['Test_Mensili_Reali', 'Test_per_Scatola', 'Fabbisogno_Kit_Mese_Stimato']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0

        df['Prodotto_Label'] = df['Descrizione'] + " [" + df['Codice'] + "]"
        return df
    except Exception as e:
        st.error(f"Errore Excel: {e}")
        return pd.DataFrame()

# --- FUNZIONI CLOUD ---
def fetch_inventory():
    try:
        df_db = conn.read(worksheet="Foglio1", ttl=0)
        magazzino = {}
        if not df_db.empty and 'Codice' in df_db.columns:
            df_db['Codice'] = df_db['Codice'].astype(str)
            for _, row in df_db.iterrows():
                cod = str(row['Codice'])
                qty = row['Quantita']
                try: scadenze = json.loads(row['Scadenze_JSON'])
                except: scadenze = []
                magazzino[cod] = {'qty': qty, 'scadenze': scadenze}
        return magazzino
    except: return {}

def update_inventory(magazzino_dict):
    data_list = []
    for cod, info in magazzino_dict.items():
        if info['qty'] > 0: 
            data_list.append({
                "Codice": cod,
                "Quantita": info['qty'],
                "Scadenze_JSON": json.dumps(info['scadenze']),
                "Ultima_Modifica": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
    
    if not data_list:
        df_new = pd.DataFrame(columns=["Codice", "Quantita", "Scadenze_JSON", "Ultima_Modifica"])
    else:
        df_new = pd.DataFrame(data_list)
        
    conn.update(worksheet="Foglio1", data=df_new)

# --- FUNZIONE GENERAZIONE PDF ---
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, f'Inventario Magazzino - {datetime.now().strftime("%d/%m/%Y")}', 0, 1, 'C')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'C')

def create_pdf_report(df_data):
    pdf = PDF()
    pdf.add_page()
    pdf.set_font('Arial', '', 10)
    
    # Raggruppa per Categoria (TIPO)
    categorie = sorted(df_data['Categoria'].unique().astype(str))
    
    for cat in categorie:
        # Intestazione Categoria
        pdf.set_fill_color(200, 220, 255) # Azzurrino
        pdf.set_font('Arial', 'B', 12)
        # Fix caratteri speciali latin-1
        cat_clean = cat.encode('latin-1', 'replace').decode('latin-1')
        pdf.cell(0, 10, f"CATEGORIA: {cat_clean}", 1, 1, 'L', fill=True)
        
        # Filtra prodotti di questa categoria
        subset = df_data[df_data['Categoria'] == cat].sort_values(by='Descrizione')
        
        # Intestazione Tabella
        pdf.set_font('Arial', 'B', 9)
        pdf.cell(30, 8, "Codice", 1)
        pdf.cell(130, 8, "Prodotto", 1)
        pdf.cell(30, 8, "Giacenza", 1)
        pdf.ln()
        
        # Righe Prodotti
        pdf.set_font('Arial', '', 9)
        for _, row in subset.iterrows():
            nome = str(row['Descrizione'])[:75].encode('latin-1', 'replace').decode('latin-1')
            cod = str(row['Codice']).encode('latin-1', 'replace').decode('latin-1')
            qta = str(int(row['Giacenza']))
            
            pdf.cell(30, 7, cod, 1)
            pdf.cell(130, 7, nome, 1)
            pdf.cell(30, 7, qta, 1)
            pdf.ln()
        
        pdf.ln(5) # Spazio tra categorie

    return pdf.output(dest='S').encode('latin-1')

# --- SIDEBAR: LOG E STAMPA ---
if 'session_log' not in st.session_state:
    st.session_state['session_log'] = []

with st.sidebar:
    st.header("🖨️ AREA STAMPA")
    st.caption("Scarica la lista completa divisa per categorie.")
    
    if st.button("📄 Genera PDF Giacenza"):
        # Prepara i dati
        df_m = load_master_data()
        df_print = df_m.copy()
        df_print['Giacenza'] = df_print['Codice'].apply(lambda x: st.session_state['magazzino'].get(x, {}).get('qty', 0))
        # Filtra solo quelli che hanno giacenza > 0 (o vuoi tutto? Mettiamo >0 per pulizia)
        df_print = df_print[df_print['Giacenza'] > 0]
        
        if not df_print.empty:
            pdf_bytes = create_pdf_report(df_print)
            st.download_button(
                label="📥 Clicca per Scaricare PDF",
                data=pdf_bytes,
                file_name=f"inventario_{datetime.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf"
            )
        else:
            st.warning("Magazzino vuoto!")

    st.divider()
    
    st.header("📋 Diario di Bordo")
    if st.session_state['session_log']:
        log_df = pd.DataFrame(st.session_state['session_log'])
        st.dataframe(log_df[['Ora', 'Azione', 'Prodotto']], hide_index=True)
    else:
        st.caption("Nessun movimento recente.")
    
    st.divider()
    if st.button("🔄 Ricarica Dati Cloud"):
        st.cache_data.clear()
        st.session_state['magazzino'] = fetch_inventory()
        st.rerun()

# --- APP PRINCIPALE ---
st.title("🏥 Gestione Lab Abbott")

df_master = load_master_data()

if 'magazzino' not in st.session_state:
    with st.spinner("⏳ Sincronizzazione Cloud..."):
        st.session_state['magazzino'] = fetch_inventory()

if not df_master.empty:
    
    tab_mov, tab_ordini, tab_scadenze = st.tabs(["⚡ OPERAZIONI", "🛒 ORDINI & ANALISI", "🗓️ SCADENZE"])

    # === TAB 1: OPERAZIONI ===
    with tab_mov:
        col_sel, col_dati = st.columns([3, 1])
        with col_sel:
            def get_label(row):
                c = str(row['Codice'])
                g = st.session_state['magazzino'].get(c, {}).get('qty', 0)
                return f"{row['Descrizione']} (Disp: {g})"
            
            opzioni = df_master.apply(get_label, axis=1).tolist()
            scelta = st.selectbox("Cerca Prodotto:", opzioni)
            desc_base = scelta.split(" (Disp:")[0]
            row_art = df_master[df_master['Descrizione'] == desc_base].iloc[0]
            codice = str(row_art['Codice'])
            
        with col_dati:
            giacenza_attuale = st.session_state['magazzino'].get(codice, {}).get('qty', 0)
            st.metric("Giacenza", f"{int(giacenza_attuale)} scatole")
            if "CAL" in str(row_art['Categoria']).upper():
                st.caption("⚠️ È un Calibratore")

        st.divider()
        c1, c2, c3 = st.columns([1, 2, 1])
        with c1:
            qty_input = st.number_input("Quantità", min_value=1, value=1, step=1)
        with c2:
            azione = st.radio("Azione:", ["➖ PRELIEVO", "➕ CARICO", "🔧 RETTIFICA (=)"], horizontal=True)
        
        scad_display, scad_sort = "-", None
        if "CARICO" in azione:
            with c3:
                cm, ca = st.columns(2)
                mm = cm.selectbox("M", range(1, 13), label_visibility="collapsed")
                yy = ca.selectbox("Y", range(datetime.now().year, datetime.now().year + 6), label_visibility="collapsed")
                scad_display = f"{mm:02d}/{yy}"
                scad_sort = f"{yy}-{mm:02d}"

        if st.button("✅ ESEGUI E SALVA", type="primary", use_container_width=True):
            if codice not in st.session_state['magazzino']:
                st.session_state['magazzino'][codice] = {'qty': 0, 'scadenze': []}
            
            ref = st.session_state['magazzino'][codice]
            log_azione = ""
            
            if "CARICO" in azione:
                ref['qty'] += qty_input
                ref['scadenze'].append({'display': scad_display, 'sort': scad_sort, 'qty': qty_input})
                ref['scadenze'].sort(key=lambda x: x['sort'])
                log_azione = "➕ Carico"

            elif "PRELIEVO" in azione:
                if ref['qty'] < qty_input:
                    st.error("Quantità insufficiente!")
                    st.stop()
                ref['qty'] -= qty_input
                rem = qty_input
                new_scad = []
                for batch in ref['scadenze']:
                    if rem > 0:
                        if batch['qty'] > rem:
                            batch['qty'] -= rem
                            rem = 0
                            new_scad.append(batch)
                        else:
                            rem -= batch['qty']
                    else:
                        new_scad.append(batch)
                ref['scadenze'] = new_scad
                log_azione = "➖ Scarico"

            elif "RETTIFICA" in azione:
                diff = qty_input - ref['qty']
                if diff == 0: st.stop()
                ref['qty'] = qty_input
                if diff > 0: ref['scadenze'].append({'display': 'MANUALE', 'sort': '9999-12', 'qty': diff})
                else:
                    da_togliere = abs(diff)
                    new_scad = []
                    for batch in ref['scadenze']:
                        if da_togliere > 0:
                            if batch['qty'] > da_togliere:
                                batch['qty'] -= da_togliere
                                da_togliere = 0
                                new_scad.append(batch)
                            else:
                                da_togliere -= batch['qty']
                        else:
                            new_scad.append(batch)
                    ref['scadenze'] = new_scad
                log_azione = "🔧 Rettifica"

            with st.status("Salvataggio...", expanded=False) as status:
                update_inventory(st.session_state['magazzino'])
                status.update(label="Salvato!", state="complete")

            st.session_state['session_log'].insert(0, {
                "Ora": datetime.now().strftime("%H:%M"),
                "Azione": log_azione,
                "Prodotto": row_art['Descrizione'][:15]+"..."
            })
            st.rerun()

    # === TAB 2: ORDINI ===
    with tab_ordini:
        st.markdown("### 🚦 Analisi Fabbisogno")
        c_search, c_filtro = st.columns([2,1])
        term = c_search.text_input("🔍 Cerca...", placeholder="Es. Urea, 8P57...")
        filtro = c_filtro.multiselect("Filtra:", ["🔴 SOTTO MINIMO", "🔴 ESAURITO", "🟡 DA ORDINARE", "🟢 OK"], default=["🔴 SOTTO MINIMO", "🔴 ESAURITO", "🟡 DA ORDINARE"])
        
        df_c = df_master.copy()
        df_c['Giacenza'] = df_c['Codice'].apply(lambda x: st.session_state['magazzino'].get(x, {}).get('qty', 0))
        
        def calcola_stato(row):
            consumo = 0
            if row['Test_Mensili_Reali'] > 0 and row['Test_per_Scatola'] > 0:
                consumo = row['Test_Mensili_Reali'] / row['Test_per_Scatola']
            elif row['Fabbisogno_Kit_Mese_Stimato'] > 0:
                consumo = row['Fabbisogno_Kit_Mese_Stimato']
            
            target = math.ceil(consumo * TARGET_MESI)
            is_cal = "CAL" in str(row['Categoria']).upper()
            if is_cal: target = max(target, MIN_SCORTA_CAL)
            
            da_ord = max(0, target - row['Giacenza'])
            
            stato = "🟢 OK"
            if is_cal and row['Giacenza'] < MIN_SCORTA_CAL: stato = "🔴 SOTTO MINIMO"
            elif row['Giacenza'] == 0: stato = "🔴 ESAURITO"
            elif da_ord > 0: stato = "🟡 DA ORDINARE"
            
            return pd.Series([stato, target, da_ord])

        df_c[['Stato', 'Target', 'Da_Ordinare']] = df_c.apply(calcola_stato, axis=1)
        
        # --- LOGICA VISUALIZZAZIONE ---
        df_view = df_c.copy()
        if filtro: df_view = df_view[df_view['Stato'].isin(filtro)]
        if term: 
            df_view = df_view[
                df_view['Descrizione'].str.contains(term, case=False, na=False) | 
                df_view['Codice'].str.contains(term, case=False, na=False) |
                df_view['Categoria'].str.contains(term, case=False, na=False)
            ]
        df_view = df_view.sort_values(by=['Da_Ordinare'], ascending=False)
        
        st.dataframe(
            df_view[['Stato', 'Categoria', 'Codice', 'Descrizione', 'Giacenza', 'Target', 'Da_Ordinare']],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Stato": st.column_config.TextColumn("Stato", width="small"),
                "Categoria": st.column_config.TextColumn("Tipo", width="small"),
                "Codice": st.column_config.TextColumn("LN Abbott", width="medium"),
                "Descrizione": st.column_config.TextColumn("Prodotto", width="large"),
                "Target": st.column_config.NumberColumn("Obiettivo"),
                "Da_Ordinare": st.column_config.NumberColumn("🛒 ORDINA")
            }
        )
        
        st.divider()
        st.write("### 📤 Esporta Ordine")
        df_export = df_c[df_c['Da_Ordinare'] > 0].copy()
        df_export = df_export[['Codice', 'Descrizione', 'Da_Ordinare', 'Confezione']]
        df_export = df_export.rename(columns={'Codice': 'Codice Prodotto', 'Da_Ordinare': 'Qta Ordine', 'Confezione': 'Conf.to'})
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df_export.to_excel(writer, index=False)
            
        st.download_button(
            label="📥 Scarica Lista Fornitore (Solo Urgenze)",
            data=buffer.getvalue(),
            file_name=f"ordine_abbott_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )

    # === TAB 3: SCADENZE ===
    with tab_scadenze:
        scad_list = []
        today = datetime.now().strftime("%Y-%m")
        limit = (datetime.now() + pd.DateOffset(months=3)).strftime("%Y-%m")
        
        for cod, data in st.session_state['magazzino'].items():
            for batch in data['scadenze']:
                if batch['sort'] < today: s = "☠️ SCADUTO"
                elif batch['sort'] <= limit: s = "⚠️ SCADE PRESTO"
                else: s = "🟢 OK"
                
                try: nome = df_master[df_master['Codice']==cod]['Descrizione'].iloc[0]
                except: nome = cod
                
                scad_list.append({"Stato": s, "Prodotto": nome, "Qta": batch['qty'], "Scadenza": batch['display']})
        
        if scad_list:
            st.dataframe(pd.DataFrame(scad_list).sort_values(by='Scadenza'), use_container_width=True, hide_index=True)
        else:
            st.info("Nessuna scadenza inserita.")

else:
    st.error("Errore Dati Master.")
