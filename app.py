import streamlit as st
import pandas as pd
from datetime import datetime
import math
import io
import json

# --- CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Magazzino con Memoria", layout="wide", initial_sidebar_state="expanded")

# COSTANTI
MESI_COPERTURA = 1.0      
MESI_BUFFER = 0.5         
TARGET_MESI = MESI_COPERTURA + MESI_BUFFER 
MIN_SCORTA_CAL = 3        

# --- CARICAMENTO DATI MASTER (Il Catalogo) ---
@st.cache_data
def load_master_data():
    try:
        df = pd.read_excel('dati.xlsx', engine='openpyxl')
        
        # Gestione Codici
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
        df = df[df['Descrizione'].notna()]
        df['Codice'] = df['Codice'].astype(str).str.replace('.0', '', regex=False)
        
        for col in ['Test_Mensili_Reali', 'Test_per_Scatola', 'Fabbisogno_Kit_Mese_Stimato']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0

        df['Prodotto_Label'] = df['Descrizione'] + " [" + df['Codice'] + "]"
        return df
    except Exception as e:
        st.error(f"Errore nel file Excel Master: {e}")
        return pd.DataFrame()

# --- MEMORIA (Session State) ---
if 'magazzino' not in st.session_state:
    st.session_state['magazzino'] = {} 
if 'storico' not in st.session_state:
    st.session_state['storico'] = []

# --- SIDEBAR: GESTIONE SALVATAGGIO ---
with st.sidebar:
    st.header("💾 Area Salvataggio")
    st.info("Carica qui l'ultimo file Excel scaricato per riprendere il lavoro.")
    
    # 1. CARICAMENTO STATO (RESTORE)
    uploaded_file = st.file_uploader("📂 Carica Backup Magazzino", type=['xlsx'])
    
    if uploaded_file is not None:
        try:
            # Legge il file di backup
            df_state = pd.read_excel(uploaded_file)
            
            # Ricostruisce il dizionario del magazzino
            new_magazzino = {}
            for index, row in df_state.iterrows():
                cod = str(row['Codice'])
                qty = row['Quantita_Totale']
                # Decodifica le scadenze dal formato testo JSON
                try:
                    scadenze = json.loads(row['Dettaglio_Scadenze_JSON'])
                except:
                    scadenze = [] # Se fallisce, lista vuota
                
                new_magazzino[cod] = {'qty': qty, 'scadenze': scadenze}
            
            # Pulsante di conferma per sovrascrivere
            if st.button("🔄 Ripristina Dati da File"):
                st.session_state['magazzino'] = new_magazzino
                st.success("Magazzino aggiornato dal file!")
                st.rerun()
                
        except Exception as e:
            st.error(f"File non valido: {e}")

    st.divider()

    # 2. SALVATAGGIO STATO (BACKUP)
    st.write("### ⬇️ Salva il lavoro")
    st.caption("Scarica questo file a fine giornata per non perdere i dati.")
    
    if st.session_state['magazzino']:
        # Prepara i dati per l'export
        export_data = []
        for cod, data in st.session_state['magazzino'].items():
            if data['qty'] > 0: # Salviamo solo ciò che esiste
                export_data.append({
                    'Codice': cod,
                    'Quantita_Totale': data['qty'],
                    'Dettaglio_Scadenze_JSON': json.dumps(data['scadenze']) # Convertiamo lista in testo
                })
        
        if export_data:
            df_export = pd.DataFrame(export_data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_export.to_excel(writer, index=False)
            
            st.download_button(
                label="💾 SCARICA BACKUP (Excel)",
                data=buffer.getvalue(),
                file_name=f"backup_magazzino_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
        else:
            st.warning("Magazzino vuoto, nulla da salvare.")
    else:
        st.write("Nessun dato in memoria.")

# --- INTERFACCIA PRINCIPALE ---
st.title("🏥 Magazzino Persistente")

df_master = load_master_data()

if not df_master.empty:
    
    # Check se il magazzino è vuoto all'avvio
    if not st.session_state['magazzino']:
        st.warning("⚠️ Il magazzino è vuoto. Usa la barra laterale per caricare un Backup o inizia a inserire prodotti.")

    tab_mov, tab_ordini, tab_scadenze = st.tabs(["⚡ Movimenti", "📦 Calcolo Ordine", "⚠️ Scadenze"])

    # === TAB 1: MOVIMENTI ===
    with tab_mov:
        col_sel, col_info = st.columns([3, 1])
        with col_sel:
            # Mostra giacenza nel menu a tendina
            def get_label(row):
                cod = str(row['Codice'])
                giacenza = st.session_state['magazzino'].get(cod, {}).get('qty', 0)
                return f"{row['Descrizione']} [Giac: {giacenza}]"
            
            # Creiamo una mappa temporanea per etichette
            opzioni = df_master.apply(get_label, axis=1).tolist()
            # Mappa inversa per trovare il codice
            scelta_label = st.selectbox("Seleziona Prodotto:", opzioni)
            
            # Trova la riga corrispondente (un po' trick per la label dinamica)
            # Recuperiamo l'indice o facciamo parsing. 
            # Metodo semplice: estrai descrizione base e cerca
            desc_base = scelta_label.split(" [Giac:")[0]
            row_art = df_master[df_master['Descrizione'] == desc_base].iloc[0]
            
        codice = row_art['Codice']
        categoria_art = str(row_art.get('Categoria', '')).upper()
        
        with col_info:
            st.info(f"Conf: {row_art.get('Confezione', '-')}")
            if "CAL" in categoria_art:
                st.warning(f"⚠️ CALIBRATORE\nMin: {MIN_SCORTA_CAL}")

        c1, c2 = st.columns([1, 2])
        with c1:
            qty = st.number_input("Quantità", min_value=1, value=1)
        with c2:
            tipo = st.radio("Azione", ["Prelievo ➖", "Carico ➕"], horizontal=True)

        scad_display, scad_sort = "-", None
        if "Carico" in tipo:
            cm, ca = st.columns(2)
            with cm: mese = st.selectbox("Mese Scad.", range(1, 13))
            with ca: anno = st.selectbox("Anno Scad.", range(datetime.now().year, datetime.now().year + 6))
            scad_display = f"{mese:02d}/{anno}"
            scad_sort = f"{anno}-{mese:02d}"

        if st.button("Registra", type="primary", use_container_width=True):
            if codice not in st.session_state['magazzino']:
                st.session_state['magazzino'][codice] = {'qty': 0, 'scadenze': []}
            
            ref = st.session_state['magazzino'][codice]
            
            if "Carico" in tipo:
                ref['qty'] += qty
                ref['scadenze'].append({'display': scad_display, 'sort': scad_sort, 'qty': qty})
                ref['scadenze'].sort(key=lambda x: x['sort'])
            else:
                if ref['qty'] < qty:
                    st.error(f"Errore: Ne hai solo {ref['qty']}!")
                    st.stop()
                ref['qty'] -= qty
                rem = qty
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
            
            st.success(f"Registrato! Nuova giacenza: {ref['qty']}")
            st.rerun() # Ricarica per aggiornare le label nel menu

    # === TAB 2: CALCOLO ORDINE ===
    with tab_ordini:
        st.markdown(f"### 📊 Calcolo Fabbisogno")
        
        df_calc = df_master.copy()
        df_calc['Giacenza'] = df_calc['Codice'].apply(lambda x: st.session_state['magazzino'].get(x, {}).get('qty', 0))
        
        def calcola_consumo_scatole(row):
            if row['Test_Mensili_Reali'] > 0 and row['Test_per_Scatola'] > 0:
                return row['Test_Mensili_Reali'] / row['Test_per_Scatola']
            if row['Fabbisogno_Kit_Mese_Stimato'] > 0:
                return row['Fabbisogno_Kit_Mese_Stimato']
            return 0 

        df_calc['Consumo_Mensile_Scatole'] = df_calc.apply(calcola_consumo_scatole, axis=1)
        
        def calcola_target(row):
            base_target = math.ceil(row['Consumo_Mensile_Scatole'] * TARGET_MESI)
            categoria = str(row['Categoria']).upper()
            if "CAL" in categoria:
                return max(base_target, MIN_SCORTA_CAL)
            return base_target

        df_calc['Scorta_Target'] = df_calc.apply(calcola_target, axis=1)
        df_calc['Da_Ordinare'] = df_calc.apply(lambda x: max(0, x['Scorta_Target'] - x['Giacenza']), axis=1)
        
        def calc_copertura(row):
            if row['Consumo_Mensile_Scatole'] <= 0: return 99.9
            return row['Giacenza'] / row['Consumo_Mensile_Scatole']
            
        df_calc['Mesi_Autonomia'] = df_calc.apply(calc_copertura, axis=1)
        
        def get_semaforo(row):
            categoria = str(row['Categoria']).upper()
            if "CAL" in categoria and row['Giacenza'] < MIN_SCORTA_CAL: return "🔴 SOTTO MINIMO (CAL)"
            if row['Consumo_Mensile_Scatole'] == 0 and "CAL" not in categoria: return "⚪ Dati mancanti"
            if row['Giacenza'] == 0: return "🔴 ESAURITO"
            if row['Mesi_Autonomia'] < MESI_BUFFER: return "🟠 URGENTE"
            if row['Da_Ordinare'] > 0: return "🟡 RIORDINARE"
            return "🟢 COPERTO"

        df_calc['Stato'] = df_calc.apply(get_semaforo, axis=1)
        
        df_view = df_calc.sort_values(by=['Da_Ordinare'], ascending=False)
        if st.checkbox("Nascondi Prodotti OK", value=True):
            df_view = df_view[df_view['Stato'] != "🟢 COPERTO"]

        st.dataframe(
            df_view[['Stato', 'Descrizione', 'Giacenza', 'Scorta_Target', 'Da_Ordinare', 'Mesi_Autonomia']],
            use_container_width=True,
            column_config={
                "Scorta_Target": st.column_config.NumberColumn("Target", help="Scorta ideale calcolata"),
                "Da_Ordinare": st.column_config.NumberColumn("🛒 DA ORDINARE"),
                "Mesi_Autonomia": st.column_config.NumberColumn("Autonomia (Mesi)", format="%.1f")
            }
        )
        
        if st.button("📥 Scarica Lista Ordine"):
            df_out = df_calc[df_calc['Da_Ordinare'] > 0][['Codice', 'Descrizione', 'Da_Ordinare', 'Confezione']]
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df_out.to_excel(writer, index=False)
            st.download_button("Download Excel", data=buffer.getvalue(), file_name="ordine_mensile.xlsx")

    # === TAB 3: SCADENZE ===
    with tab_scadenze:
        st.markdown("### 📅 Controllo Scadenze")
        scad_list = []
        today_str = datetime.now().strftime("%Y-%m")
        limit_str = (datetime.now() + pd.DateOffset(months=3)).strftime("%Y-%m")
        
        for cod, data in st.session_state['magazzino'].items():
            for batch in data['scadenze']:
                status = "🟢"
                if batch['sort'] < today_str: status = "☠️ SCADUTO"
                elif batch['sort'] <= limit_str: status = "⚠️ SCADE A BREVE"
                
                # Nome prodotto
                try:
                    nome = df_master[df_master['Codice']==cod]['Descrizione'].iloc[0]
                except:
                    nome = f"Codice {cod}"

                scad_list.append({
                    "Stato": status,
                    "Prodotto": nome,
                    "Quantità": batch['qty'],
                    "Scadenza": batch['display'],
                    "Sort": batch['sort']
                })
        
        if scad_list:
            df_scad = pd.DataFrame(scad_list).sort_values(by='Sort')
            st.dataframe(df_scad[['Stato', 'Prodotto', 'Quantità', 'Scadenza']], use_container_width=True)
        else:
            st.info("Nessuna scadenza critica.")

else:
    st.error("Errore: File dati.xlsx mancante su GitHub.")
