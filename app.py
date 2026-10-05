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
MESI_BUFFER = 0.50        
TARGET_MESI = MESI_COPERTURA + MESI_BUFFER 
MIN_SCORTA_CAL = 5        

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
        
        if 'Confezione' not in df.columns:
            df['Confezione'] = ""
            
        df = df[df['Descrizione'].notna() & df['Codice'].notna()] 
        df['Codice'] = df['Codice'].astype(str).str.replace('.0', '', regex=False)
        
        # --- INIEZIONE VIRTUALE PRODOTTI MANCANTI DALL'EXCEL ---
        prodotti_volanti = [
            {'Codice': '06Q1061', 'Descrizione': 'GLP systems Track Recap LARGE', 'Categoria': 'CONS', 'Fabbisogno_Kit_Mese_Stimato': 7, 'Assay_Name': ''},
            {'Codice': '06Q1051', 'Descrizione': 'GLP systems Track Recaps SMALL', 'Categoria': 'CONS', 'Fabbisogno_Kit_Mese_Stimato': 14, 'Assay_Name': ''},
            {'Codice': '06Q1402', 'Descrizione': 'GLP system Track Secondary Tubes (PUSH)', 'Categoria': 'CONS', 'Fabbisogno_Kit_Mese_Stimato': 20, 'Assay_Name': ''},
            {'Codice': '6T2101', 'Descrizione': 'Secchi Rifiuti GLP Catena (9pz)', 'Categoria': 'CONS', 'Fabbisogno_Kit_Mese_Stimato': 2, 'Assay_Name': ''}
        ]
        
        codici_puliti = df['Codice'].astype(str).str.replace('-', '', regex=False).str.upper().tolist()
        nuove_righe = []
        
        for p in prodotti_volanti:
            if p['Codice'] not in codici_puliti:
                nuove_righe.append(p)
                
        if nuove_righe:
