import streamlit as st
import pandas as pd
from datetime import datetime
import io

# Configurazione della pagina
st.set_page_config(page_title="Gestione Magazzino Abbott", layout="wide")

# Funzione per caricare i dati (adattata al TUO file specifico)
def load_data():
    try:
        # Legge il file excel
        df = pd.read_excel('dati.xlsx', engine='openpyxl')
        
        # 1. Rinomina le colonne del tuo Excel in nomi standard per l'app
        # Mappa: Nome nel tuo Excel -> Nome nell'App
        column_mapping = {
            'LN ABBOTT': 'Codice',
            'Descrizione commerciale': 'Descrizione',
            'Rgt/Cal/QC/Cons': 'Categoria',
            'Conf.to': 'Confezione',
            'LOB': 'Reparto',
            '# Kit/Mese': 'Consumo_Mensile' # Prendo questa come riferimento se serve
        }
        
        # Rinomina solo le colonne che trova
        df = df.rename(columns=column_mapping)
        
        # 2. Pulizia Dati
        # Mantiene solo le righe che hanno almeno una Descrizione valida
        df = df[df['Descrizione'].notna()]
        
        # Riempie i valori vuoti per estetica
        df['Codice'] = df['Codice'].fillna('-')
        df['Categoria'] = df['Categoria'].fillna('Altro')
        df['Reparto'] = df['Reparto'].fillna('Generale')
        
        # Crea una colonna univoca per la ricerca (Codice + Descrizione)
        df['Prodotto_Completo'] = df['Codice'].astype(str) + " - " + df['Descrizione']
        
        return df
    except Exception as e:
        st.error(f"Errore nel caricamento del file dati.xlsx: {e}")
        return pd.DataFrame()

# Inizializzazione Session State (Memoria dell'app)
if 'movimenti' not in st.session_state:
    st.session_state['movimenti'] = []

# --- INTERFACCIA UTENTE ---

st.title("📦 Gestione Magazzino - Abbott Alinity")

# Caricamento dati
df_prodotti = load_data()

if not df_prodotti.empty:
    
    # 1. SEZIONE INSERIMENTO MOVIMENTO
    st.header("Nuovo Movimento")
    
    col1, col2, col3 = st.columns([3, 1, 1])
    
    with col1:
        # Menu a tendina con ricerca
        lista_prodotti = df_prodotti['Prodotto_Completo'].tolist()
        prodotto_selezionato = st.selectbox("Cerca Prodotto (Scrivi nome o codice)", lista_prodotti)
    
    with col2:
        quantita = st.number_input("Quantità", min_value=1, value=1, step=1)
        
    with col3:
        tipo_movimento = st.radio("Tipo", ["Prelievo ➖", "Carico ➕"], horizontal=True)

    if st.button("Registra Movimento", type="primary"):
        # Recupera i dettagli del prodotto selezionato
        dettagli = df_prodotti[df_prodotti['Prodotto_Completo'] == prodotto_selezionato].iloc[0]
        
        ora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        segno = "-" if "Prelievo" in tipo_movimento else "+"
        
        nuovo_movimento = {
            "Data": ora,
            "Codice": dettagli['Codice'],
            "Descrizione": dettagli['Descrizione'],
            "Categoria": dettagli['Categoria'],
            "Reparto": dettagli['Reparto'],
            "Quantità": f"{segno}{quantita}",
            "Confezione": dettagli['Confezione']
        }
        
        # Aggiunge in cima alla lista
        st.session_state['movimenti'].insert(0, nuovo_movimento)
        st.success(f"Registrato: {segno}{quantita} x {dettagli['Descrizione']}")

    st.divider()

    # 2. SEZIONE STORICO E EXPORT
    st.header("📝 Storico Movimenti")

    if st.session_state['movimenti']:
        # Crea DataFrame dallo storico
        df_storico = pd.DataFrame(st.session_state['movimenti'])
        
        # Mostra tabella colorata
        st.dataframe(df_storico, use_container_width=True)
        
        # Bottone Export Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df_storico.to_excel(writer, index=False, sheet_name='Movimenti')
            
        st.download_button(
            label="📥 Scarica Excel Movimenti",
            data=buffer.getvalue(),
            file_name=f"movimenti_magazzino_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        if st.button("🗑️ Cancella tutto lo storico"):
            st.session_state['movimenti'] = []
            st.rerun()
            
    else:
        st.info("Nessun movimento registrato in questa sessione.")

    # 3. VISUALIZZAZIONE DATI MASTER (Opzionale, per controllo)
    with st.expander("🔍 Vedi Lista Completa Prodotti (Database)"):
        st.dataframe(df_prodotti[['Reparto', 'Categoria', 'Codice', 'Descrizione', 'Confezione']])

else:
    st.warning("Il file dati.xlsx sembra vuoto o non leggibile. Controlla di averlo caricato su GitHub.")
