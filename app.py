import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import io

# Configurazione Pagina
st.set_page_config(page_title="Abbott Manager Pro", layout="wide")

# --- FUNZIONI DI CARICAMENTO ---
@st.cache_data
def load_master_data():
    try:
        # Carica il file Excel
        df = pd.read_excel('dati.xlsx', engine='openpyxl')
        
        # Mappatura colonne (Adattata al tuo file specifico)
        # Assicurati che nel tuo Excel la colonna dei consumi si chiami '# Kit/Mese' o simile
        col_map = {
            'LN ABBOTT': 'Codice',
            'Descrizione commerciale': 'Descrizione',
            'Rgt/Cal/QC/Cons': 'Categoria',
            '# Kit/Mese': 'Fabbisogno_Mensile', 
            'Conf.to': 'Confezione'
        }
        
        # Rinomina e pulisci
        df = df.rename(columns=col_map)
        df = df[df['Descrizione'].notna()] # Rimuove righe vuote
        
        # Converte il fabbisogno in numeri (gestisce errori se c'è testo)
        df['Fabbisogno_Mensile'] = pd.to_numeric(df['Fabbisogno_Mensile'], errors='coerce').fillna(0)
        
        # Crea chiave univoca
        df['Prodotto_Label'] = df['Codice'].astype(str) + " | " + df['Descrizione']
        
        return df[['Codice', 'Descrizione', 'Categoria', 'Fabbisogno_Mensile', 'Confezione', 'Prodotto_Label']]
    except Exception as e:
        st.error(f"Errore caricamento dati: {e}")
        return pd.DataFrame()

# --- GESTIONE STATO (MEMORIA) ---
# Qui simuliamo il magazzino reale. 
# NOTA: Quando ricarichi la pagina web, questo si resetta se non colleghi un database esterno.
if 'magazzino_virtuale' not in st.session_state:
    # Dizionario: Codice -> Quantità Attuale
    st.session_state['magazzino_virtuale'] = {} 

if 'storico_movimenti' not in st.session_state:
    st.session_state['storico_movimenti'] = []

# --- INTERFACCIA ---
st.title("🏥 Abbott Alinity - Smart Manager")

df_master = load_master_data()

if not df_master.empty:
    
    # Creiamo due schede: Operatività e Analisi
    tab1, tab2 = st.tabs(["📦 Movimenti & Magazzino", "📊 Dashboard Ordini & Allarmi"])

    # === TAB 1: OPERATIVITÀ ===
    with tab1:
        st.subheader("Registra Entrate/Uscite")
        
        col_in1, col_in2, col_in3 = st.columns([3, 1, 1])
        
        with col_in1:
            prod_list = df_master['Prodotto_Label'].tolist()
            prodotto_scelto = st.selectbox("Seleziona Prodotto", prod_list)
        
        with col_in2:
            qty = st.number_input("Quantità (Scatole)", min_value=1, value=1)
            
        with col_in3:
            azione = st.radio("Azione", ["Prelevo (Uso)", "Carico (Arrivo)"], label_visibility="collapsed")

        # Opzione Scadenza (Solo se carico)
        scadenza_input = None
        if "Carico" in azione:
            scadenza_input = st.date_input("Scadenza (Opzionale)", value=None)

        if st.button("Registra Movimento", type="primary"):
            # Logica aggiornamento
            row = df_master[df_master['Prodotto_Label'] == prodotto_scelto].iloc[0]
            codice = row['Codice']
            
            # Aggiorna Giacenza Virtuale
            current_stock = st.session_state['magazzino_virtuale'].get(codice, 0)
            
            if "Carico" in azione:
                nuova_giacenza = current_stock + qty
                segno = "+"
            else:
                nuova_giacenza = max(0, current_stock - qty) # Non andiamo sotto zero
                segno = "-"
                
            st.session_state['magazzino_virtuale'][codice] = nuova_giacenza
            
            # Registra nello storico
            movimento = {
                "Data": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "Prodotto": row['Descrizione'],
                "Codice": codice,
                "Azione": "Carico" if "Carico" in azione else "Prelievo",
                "Quantità": f"{segno}{qty}",
                "Giacenza Post-Mov": nuova_giacenza,
                "Scadenza": scadenza_input.strftime("%d/%m/%Y") if scadenza_input else "-"
            }
            st.session_state['storico_movimenti'].insert(0, movimento)
            st.success(f"Registrato! Nuova giacenza stimata: {nuova_giacenza}")

        st.divider()
        
        # Tabella Movimenti Recenti
        st.write("📝 Ultimi Movimenti")
        if st.session_state['storico_movimenti']:
            st.dataframe(pd.DataFrame(st.session_state['storico_movimenti']), use_container_width=True)

    # === TAB 2: DASHBOARD INTELLIGENTE ===
    with tab2:
        st.header("Analisi Fabbisogno e Ordini")
        st.info("Questa tabella confronta la tua giacenza attuale con il consumo mensile previsto dal file Excel.")
        
        # Creiamo il DataFrame per l'analisi
        # 1. Prendiamo il master
        df_analisi = df_master.copy()
        
        # 2. Aggiungiamo la colonna "Giacenza Attuale" prendendola dalla memoria dell'app
        df_analisi['Giacenza_Attuale'] = df_analisi['Codice'].map(st.session_state['magazzino_virtuale']).fillna(0)
        
        # 3. Calcoli Intelligenti
        # Copertura: Quanti mesi copro con la giacenza attuale?
        # Se consumo è 0, evito divisione per zero
        df_analisi['Copertura_Mesi'] = df_analisi.apply(
            lambda x: round(x['Giacenza_Attuale'] / x['Fabbisogno_Mensile'], 1) if x['Fabbisogno_Mensile'] > 0 else 99, axis=1
        )
        
        # Da Ordinare: Se ho meno del fabbisogno, suggerisci la differenza
        df_analisi['DA_ORDINARE'] = df_analisi.apply(
            lambda x: max(0, x['Fabbisogno_Mensile'] - x['Giacenza_Attuale']), axis=1
        )
        
        # Status (Semaforo)
        def get_status(row):
            if row['Fabbisogno_Mensile'] == 0:
                return "⚪ Non definito"
            if row['Giacenza_Attuale'] >= row['Fabbisogno_Mensile']:
                return "🟢 OK"
            elif row['Giacenza_Attuale'] < (row['Fabbisogno_Mensile'] * 0.2): # Meno del 20%
                return "🔴 CRITICO"
            else:
                return "🟡 ORDINARE" # Sotto soglia ma non a zero

        df_analisi['Stato'] = df_analisi.apply(get_status, axis=1)
        
        # Ordiniamo per urgenza (Prima i rossi)
        df_analisi = df_analisi.sort_values(by=['Copertura_Mesi'])
        
        # Filtri
        filtro_stato = st.multiselect("Filtra per Stato", ["🔴 CRITICO", "🟡 ORDINARE", "🟢 OK"], default=["🔴 CRITICO", "🟡 ORDINARE"])
        if filtro_stato:
            df_view = df_analisi[df_analisi['Stato'].isin(filtro_stato)]
        else:
            df_view = df_analisi

        # Visualizzazione con colori
        st.dataframe(
            df_view[['Stato', 'Descrizione', 'Giacenza_Attuale', 'Fabbisogno_Mensile', 'DA_ORDINARE', 'Copertura_Mesi']],
            use_container_width=True,
            column_config={
                "Stato": st.column_config.TextColumn("Status"),
                "Copertura_Mesi": st.column_config.ProgressColumn("Copertura Mese", min_value=0, max_value=2, format="%.1f mesi"),
            }
        )
        
        # EXPORT ORDINE
        st.write("### 📤 Esporta Lista Ordine")
        # Filtra solo quelli che hanno qualcosa da ordinare
        df_ordine = df_analisi[df_analisi['DA_ORDINARE'] > 0][['Codice', 'Descrizione', 'DA_ORDINARE', 'Confezione']]
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df_ordine.to_excel(writer, index=False, sheet_name='Proposta_Ordine')
            
        st.download_button(
            label="Scarica Excel Ordini Suggeriti",
            data=buffer.getvalue(),
            file_name=f"ordine_suggerito_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

else:
    st.error("Non trovo dati.xlsx o il file è vuoto.")
