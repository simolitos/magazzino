import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, timedelta
import math
import io
import json
from fpdf import FPDF
import time

# --- CONFIGURAZIONE ---
st.set_page_config(page_title="VIRTUAL Magazzino", layout="wide", initial_sidebar_state="expanded")

# --- STILE CSS ---
st.markdown("""
    <style>
    .title-text {
        font-size: 38px;
        font-weight: 800;
        color: #1E3A8A;
        margin-bottom: 0px;
    }
    .credits {
        font-size: 14px;
        font-style: italic;
        color: #64748B;
        vertical-align: middle;
        margin-left: 10px;
    }
    .block-container {
        padding-top: 2rem;
    }
    
    /* CUSTOM LOADER */
    .stSpinner { display: none; }
    #custom-loader {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background-color: rgba(255, 255, 255, 0.85);
        backdrop-filter: blur(5px);
        z-index: 999999;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    }
    .spinner {
        width: 50px;
        height: 50px;
        border: 5px solid #f3f3f3;
        border-top: 5px solid #1E3A8A;
        border-radius: 50%;
        animation: spin 1s linear infinite;
        margin-bottom: 15px;
    }
    .loading-text {
        font-family: 'Arial', sans-serif;
        font-size: 18px;
        font-weight: 600;
        color: #1E3A8A;
        animation: pulse 1.5s infinite;
    }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    @keyframes pulse { 0% { opacity: 0.6; } 50% { opacity: 1; } 100% { opacity: 0.6; } }
    </style>
    """, unsafe_allow_html=True)

# --- PARAMETRI ---
MESI_COPERTURA = 1.0      
MESI_BUFFER = 0.25        
TARGET_MESI = MESI_COPERTURA + MESI_BUFFER 
MIN_SCORTA_CAL = 3        

# --- CONNESSIONE ---
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
except:
    st.error("⚠️ Errore Segreti: Configura .streamlit/secrets.toml")
    st.stop()

# --- DATI MASTER ---
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
            'Conf.to': 'Confezione',
            'Assay name': 'Assay_Name'
        }
        
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        
        df = df[df['Descrizione'].notna() & df['Codice'].notna()] 
        df['Codice'] = df['Codice'].astype(str).str.replace('.0', '', regex=False)
        df['Categoria'] = df['Categoria'].astype(str).fillna('')
        df['Assay_Name'] = df['Assay_Name'].astype(str).fillna('')
        
        def clean_custom_values(val):
            if pd.isna(val): return val
            s = str(val).strip()
            if "25-30" in s: return 30        
            if "28" in s and "?" in s: return 4   
            if "12/15" in s: return 15        
            return val

        df['Fabbisogno_Kit_Mese_Stimato'] = df['Fabbisogno_Kit_Mese_Stimato'].apply(clean_custom_values)
        
        # --- FORZATURE PREVENTIVE UNIFICATE ---
        df.loc[df['Codice'].str.contains("8P0852|8P08-52", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 2
        df.loc[df['Codice'].str.contains("9P4922|9P49-22", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 4
        df.loc[df['Codice'].str.contains("7P5320|7P53-20", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 2
        df.loc[df['Codice'].str.contains("06Q1461|06Q14-61", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 9
        df.loc[df['Codice'].str.contains("1R3801|1R38-01", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 6
        df.loc[df['Codice'].str.contains("6P1401|6P14-01", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 45
        df.loc[df['Codice'].str.contains("8P9870|8P98-70", case=False, na=False), 'Fabbisogno_Kit_Mese_Stimato'] = 1
        
        # FIX MC MCC CALS: Forziamo la categoria a "CAL" e il nome a "MC MCC CALS"
        df.loc[df['Codice'].str.contains("08P6001|08P60-01", case=False, na=False), 'Descrizione'] = 'MC MCC CALS'
        df.loc[df['Codice'].str.contains("08P6001|08P60-01", case=False, na=False), 'Categoria'] = 'CAL'
        
        df['Kit_Mese_Numeric'] = pd.to_numeric(df['Fabbisogno_Kit_Mese_Stimato'], errors='coerce')
        
        for col in ['Test_Mensili_Reali', 'Test_per_Scatola']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0
                
        df.loc[df['Codice'].str.contains("09P2820|09P28-20", case=False, na=False), 'Test_Mensili_Reali'] = 1000
                
        def calcola_kit_mancanti(row):
            if pd.isna(row['Kit_Mese_Numeric']) or row['Kit_Mese_Numeric'] == 0:
                if row['Test_Mensili_Reali'] > 0 and row['Test_per_Scatola'] > 0:
                    return row['Test_Mensili_Reali'] / row['Test_per_Scatola']
            return row['Kit_Mese_Numeric']
            
        df['Kit_Mese_Numeric'] = df.apply(calcola_kit_mancanti, axis=1)
        df['Kit_Mese_Numeric'] = df['Kit_Mese_Numeric'].fillna(0)
        
        # --- REGOLE DI INCLUSIONE NEL MAGAZZINO ---
        has_valid_consumption = df['Kit_Mese_Numeric'] > 0
        is_cal = df['Categoria'].str.upper().str.contains("CAL", na=False)
        
        # Paracadute per i prodotti speciali
        is_special = df['Descrizione'].str.contains("VANCOMICINA|BARBITURICI|TRAB|HBsAg Quant|Tireoglobulina|ICT SAMPLE DILUENT|Omocisteina|SECONDARY TUBES|Sample Cups|Reaction Vessels|Maintenance Solutions|MC MCC CALS", case=False, na=False) | \
                     df['Assay_Name'].str.contains("VANCOMICINA|BARBITURICI|TRAB|HBsAg Quant|Tireoglobulina|ICT SAMPLE DILUENT|Omocisteina|SECONDARY TUBES|Sample Cups|Reaction Vessels|Maintenance Solutions|MC MCC CALS", case=False, na=False) | \
                     df['Codice'].str.contains("8P0852|9P4922|7P5320|09P2820|06Q1461|1R3801|6P1401|8P9870|08P6001", case=False, na=False)
        
        df = df[has_valid_consumption | is_cal | is_special]

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

def manage_log_cloud(azione, prodotto_nome, qta):
    try:
        try: df_log = conn.read(worksheet="Logs", ttl=0)
        except: df_log = pd.DataFrame(columns=["Timestamp", "Data_Leggibile", "Azione", "Prodotto"])

        now = datetime.now()
        new_row = {
            "Timestamp": now,
            "Data_Leggibile": now.strftime("%d/%m %H:%M"),
            "Azione": f"{azione} ({qta})",
            "Prodotto": prodotto_nome
        }
        
        df_log = pd.concat([pd.DataFrame([new_row]), df_log], ignore_index=True)
        
        days_ago_7 = now - timedelta(days=7)
        df_log['Timestamp'] = pd.to_datetime(df_log['Timestamp'])
        df_log_clean = df_log[df_log['Timestamp'] > days_ago_7]
        
        conn.update(worksheet="Logs", data=df_log_clean)
        return df_log_clean
    except Exception as e:
        return pd.DataFrame()

def fetch_only_log():
    try:
        df_log = conn.read(worksheet="Logs", ttl=0)
        if not df_log.empty:
            df_log['Timestamp'] = pd.to_datetime(df_log['Timestamp'])
            df_log = df_log.sort_values(by='Timestamp', ascending=False)
        return df_log
    except: return pd.DataFrame()

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
    categorie = sorted(df_data['Categoria'].unique().astype(str))
    for cat in categorie:
        pdf.set_fill_color(200, 220, 255)
        pdf.set_font('Arial', 'B', 12)
        cat_clean = cat.encode('latin-1', 'replace').decode('latin-1')
        pdf.cell(0, 10, f"CATEGORIA: {cat_clean}", 1, 1, 'L', fill=True)
        
        subset = df_data[df_data['Categoria'] == cat].sort_values(by='Descrizione')
        
        pdf.set_font('Arial', 'B', 9)
        pdf.cell(30, 8, "Codice", 1)
        pdf.cell(130, 8, "Prodotto", 1)
        pdf.cell(30, 8, "Giacenza", 1)
        pdf.ln()
        
        pdf.set_font('Arial', '', 9)
        for _, row in subset.iterrows():
            nome = str(row['Descrizione'])[:75].encode('latin-1', 'replace').decode('latin-1')
            cod = str(row['Codice']).encode('latin-1', 'replace').decode('latin-1')
            qta = str(int(row['Giacenza']))
            pdf.cell(30, 7, cod, 1)
            pdf.cell(130, 7, nome, 1)
            pdf.cell(30, 7, qta, 1)
            pdf.ln()
        pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1')

# --- HEADER ---
st.markdown("""
    <div>
        <span class='title-text'>🌐 VirtuaL: Magazzino Abbott-LT</span>
        <span class='credits'>@SimoneR</span>
    </div>
    """, unsafe_allow_html=True)
st.divider()

# --- SIDEBAR ---
with st.sidebar:
    st.header("🖨️ STAMPA")
    if st.button("📄 Genera PDF Giacenza"):
        df_m = load_master_data()
        if 'magazzino' in st.session_state:
            df_print = df_m.copy()
            df_print['Giacenza'] = df_print['Codice'].apply(lambda x: st.session_state['magazzino'].get(x, {}).get('qty', 0))
            df_print = df_print[df_print['Giacenza'] > 0]
            if not df_print.empty:
                pdf_bytes = create_pdf_report(df_print)
                st.download_button("📥 Scarica PDF", data=pdf_bytes, file_name=f"inventario_{datetime.now().strftime('%Y%m%d')}.pdf", mime="application/pdf")
            else: st.warning("Magazzino vuoto!")

    st.divider()
    st.header("📋 LOG (Ultimi 7gg)")
    
    if 'cloud_log' not in st.session_state:
        st.session_state['cloud_log'] = fetch_only_log()
    
    if st.button("🔄 Aggiorna Log"):
        st.session_state['cloud_log'] = fetch_only_log()
        st.rerun()

    if not st.session_state['cloud_log'].empty:
        show_log = st.session_state['cloud_log'][['Data_Leggibile', 'Azione', 'Prodotto']].head(50)
        st.dataframe(show_log, hide_index=True, use_container_width=True)
    else:
        st.caption("Nessun evento recente.")

df_master = load_master_data()

if 'magazzino' not in st.session_state:
    with st.spinner("⏳ Sincronizzazione Cloud..."):
        st.session_state['magazzino'] = fetch_inventory()

if not df_master.empty:
    
    tab_mov, tab_ordini, tab_scadenze = st.tabs(["⚡
