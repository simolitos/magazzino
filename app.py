import streamlit as st
import pandas as pd
import math

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Ordini Laboratorio", page_icon="🧪", layout="wide")

# --- FUNZIONE CARICAMENTO DATI ---
@st.cache_data
def load_data():
    # Cerca il file Excel. Assicurati di caricarlo su GitHub con questo nome esatto:
    file_name = "dati.xlsx" 
    
    try:
        # Legge il file Excel
        df = pd.read_excel(file_name)
        
        # MAPPATURA COLONNE (Adatto il codice ai nomi del tuo file originale)
        # Il tuo file ha queste colonne: 'LOB', 'Rgt/Cal/QC/Cons', 'Descrizione commerciale', 'KIT', 'Test TOT MEDI/MESE Aggiustati'
        
        # Rinomino le colonne per comodità
        rename_map = {
            'Rgt/Cal/QC/Cons': 'Tipo',
            'Descrizione commerciale': 'Prodotto',
            'KIT': 'Test_per_Kit',
            'Test TOT MEDI/MESE Aggiustati': 'Fabbisogno_Mensile'
        }
        
        # Tengo solo le colonne utili e rinomino
        useful_cols = ['LOB'] + list(rename_map.keys())
        # Filtro per evitare errori se mancano colonne
        available_cols = [c for c in useful_cols if c in df.columns]
        df_clean = df[available_cols].rename(columns=rename_map)
        
        # Pulizia dati numerici (trasforma errori o vuoti in 0)
        df_clean['Test_per_Kit'] = pd.to_numeric(df_clean['Test_per_Kit'], errors='coerce').fillna(0)
        df_clean['Fabbisogno_Mensile'] = pd.to_numeric(df_clean['Fabbisogno_Mensile'], errors='coerce').fillna(0)
        
        return df_clean
        
    except FileNotFoundError:
        return None

# --- INTERFACCIA UTENTE ---
st.title("🧪 Magazzino & Ordini")

# Caricamento
if 'data' not in st.session_state:
    st.session_state.data = load_data()

df = st.session_state.data

if df is None:
    st.error("⚠️ File 'dati.xlsx' non trovato!")
    st.info("Carica il tuo file Excel originale su GitHub e rinominalo 'dati.xlsx'.")
else:
    # --- 1. FILTRI LATERALI ---
    with st.expander("🔎 Filtra Prodotti", expanded=True):
        col_f1, col_f2 = st.columns(2)
        lob_list = df['LOB'].dropna().unique().tolist()
        tipo_list = df['Tipo'].dropna().unique().tolist()
        
        with col_f1:
            sel_lob = st.multiselect("Reparto", options=lob_list, default=lob_list[:1]) # Default seleziona il primo
        with col_f2:
            sel_tipo = st.multiselect("Tipo", options=tipo_list, default=['RGT'])

    # Logica Filtro
    mask = (df['LOB'].isin(sel_lob)) & (df['Tipo'].isin(sel_tipo))
    df_filtered = df[mask].copy()

    # Aggiungi colonna Giacenza per l'input (inizia a 0)
    if 'Giacenza' not in df_filtered.columns:
        df_filtered['Giacenza'] = 0

    st.write(f"Trovati **{len(df_filtered)}** prodotti.")

    # --- 2. TABELLA DI INSERIMENTO ---
    # Questa è la parte magica modificabile
    edited_df = st.data_editor(
        df_filtered[['Prodotto', 'Test_per_Kit', 'Fabbisogno_Mensile', 'Giacenza']],
        column_config={
            "Prodotto": st.column_config.TextColumn("Nome", disabled=True),
            "Test_per_Kit": st.column_config.NumberColumn("Test/Kit", disabled=True, format="%d"),
            "Fabbisogno_Mensile": st.column_config.NumberColumn("Consumo Mese", disabled=True),
            "Giacenza": st.column_config.NumberColumn(
                "📦 TUE SCATOLE", 
                help="Quante ne hai in frigo?",
                min_value=0, 
                step=1,
                format="%d"
            )
        },
        use_container_width=True,
        hide_index=True,
        height=450
    )

    # --- 3. BOTTONE CALCOLO ---
    if st.button("CALCOLA ORDINE 🚀", type="primary", use_container_width=True):
        ordini = []
        
        for index, row in edited_df.iterrows():
            giacenza = row['Giacenza']
            test_per_kit = row['Test_per_Kit']
            fabbisogno = row['Fabbisogno_Mensile']
            nome = row['Prodotto']
            
            da_ordinare_pz = 0
            motivo = ""

            # Calcolo test disponibili
            copertura_test = giacenza * test_per_kit

            # LOGICA DI ORDINE
            # Caso 1: Reagenti con fabbisogno definito
            if fabbisogno > 5: # Soglia minima per considerare il fabbisogno "reale"
                if copertura_test < fabbisogno:
                    mancanti = fabbisogno - copertura_test
                    if test_per_kit > 0:
                        da_ordinare_pz = math.ceil(mancanti / test_per_kit)
                    else:
                        da_ordinare_pz = 1
                    motivo = "Sotto scorta"
            
            # Caso 2: Calibratori o prodotti a basso consumo (Fabbisogno basso o 0)
            # Se ne ho 0, ne ordino 1 per sicurezza
            elif giacenza == 0:
                da_ordinare_pz = 1
                motivo = "Scorta minima (0 in casa)"

            if da_ordinare_pz > 0:
                ordini.append({
                    "Prodotto": nome,
                    "Da Ordinare": da_ordinare_pz,
                    "Motivo": motivo
                })

        st.divider()
        
        if ordini:
            st.success(f"Devi ordinare {len(ordini)} articoli!")
            df_res = pd.DataFrame(ordini)
            st.dataframe(df_res, use_container_width=True)
        else:
            st.balloons()
            st.info("✅ Tutto a posto! Non serve ordinare nulla con queste quantità.")
