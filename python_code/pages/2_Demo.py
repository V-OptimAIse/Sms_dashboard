import random
import numpy as np
import pandas as pd
import streamlit as st
import time
import INIT_PARAMS as IPS
from pathlib import Path

# Define the initial value of current_flow_rate
if 'flow_rate' not in st.session_state:
    st.session_state.flow_rate = []
if 'fill_level' not in st.session_state:
    st.session_state.fill_level = 0
if 'threshold' not in st.session_state:
    st.session_state.threshold = 0
if 'df_op' not in st.session_state:
    st.session_state.df_op = None


# Function to update the Streamlit app with sensor data
def update_streamlit():
    global initial_sensor_reading
    count = 0
    sensor_reading = initial_sensor_reading
    while True:
        # Simulate sensor reading (replace with actual sensor code)
        if count == 0:
            st.session_state.flow_rate = []
        flow_ = np.round(0.05 * random.random(), 3)
        st.session_state.flow_rate.append(flow_)  # 0 to 0.05 fill rate random
        sensor_reading_new = sensor_reading - st.session_state.flow_rate[count]  # Simulated sensor reading
        st.session_state.fill_level = min(int(100 * (initial_sensor_reading - sensor_reading)), 100)    # Simulated fill level

        wt_raw = np.round(st.session_state.fill_level * 0.01 * 3.142 * IPS.DENSITY * 8, 2)
        wt_mdld = np.round(wt_raw + np.random.randint(-50, 100) * 0.01, 2)

        etf = np.round((st.session_state.threshold - st.session_state.fill_level) / np.mean(np.array(st.session_state.flow_rate)), 2)
        etf_c = etf if etf >= 0 else 0

        # Update the Streamlit app
        fill_level_placeholder.metric(label="Current Fill Level", value=str(st.session_state.fill_level) + " Units")
        flow_rate_placeholder.metric(label="Current Flow Rate", value=str(flow_) + " Tonnes/minute")

        wt_raw_placeholder.metric(label="Weight Observed", value=str(wt_raw) + " Tonnes")
        wt_mdld_placeholder.metric(label="Modelled Weight", value=str(wt_mdld) + " Tonnes")

        etf_placeholder.metric(label="Estimated time to Fill", value=str(np.round(etf_c/10,2)) + " seconds")

        if st.session_state.fill_level >= st.session_state.threshold:
            status_light.image(status_images['Red'], width=IPS.LIGHT_IMG_WIDTH)
            flow_rate_placeholder.metric(label="Current Flow Rate", value=str(0) + " Tonnes/minute")
            break
        elif st.session_state.fill_level >= st.session_state.threshold * IPS.YELLOW_PCT:
            status_light.image(status_images['Yellow'], width=IPS.LIGHT_IMG_WIDTH)
        else:
            status_light.image(status_images['Green'], width=IPS.LIGHT_IMG_WIDTH)

        ladle_img_path = Path(__file__).parent.parent / 'LadleImages' / f'Ladle_Image_{st.session_state.fill_level}.png'
        bucket_image.image(str(ladle_img_path), width=IPS.LADLE_IMG_WIDTH,
                           caption=f'Fill Level: {int(st.session_state.fill_level)}%')

        # Sleep for a short interval
        time.sleep(0.3)
        count += 1
        sensor_reading = sensor_reading_new

# Load Operator file
operator_sheet = Path(__file__).parent.parent / 'OperatorDetails' / 'OperatorDetails.xlsx'
st.session_state.df_op = pd.read_excel(operator_sheet)
st.session_state.df_op = st.session_state.df_op.set_index('Operator', drop=True)

# Create columns for layout
left_col, right_col = st.columns(2)

with st.sidebar.form(key="Operator Details"):
    st.sidebar.header('Operator Details')
    operator_names = st.session_state.df_op.index.values.tolist()
    operator_name = st.sidebar.selectbox('Operator Name', (operator_names))
    performance_val = int(100 * st.session_state.df_op.at[operator_name, 'Adhered']/st.session_state.df_op.at[operator_name, 'Runs'])
    operator_stopped_count = st.sidebar.metric('Performance %', value=performance_val)
    op_details_submitted = st.form_submit_button("Submit Details")
    if op_details_submitted:
        st.sidebar.success("Operator Details Submitted")

with st.sidebar.form(key="Ladle Details"):
    st.sidebar.header('Ladle Details')
    ladle_id = st.sidebar.selectbox('Ladle ID', ('27AX', '32AV', '21AG', '27AV'))
    tlc_stand = st.sidebar.selectbox('TLC Stand', ('1', '2'))
    ladle_details_submitted = st.form_submit_button("Submit Ladle")
    if ladle_details_submitted:
        st.sidebar.success("Ladle Details Submitted")

# Left column with Fluid Information and Status Lights
with left_col:
    with st.form(key="Hotmetal Details"):
        st.header('Ladle Information')
        st.session_state.threshold = st.number_input('Threshold Level', min_value=0.0, value=100.0)
        fill_level_placeholder = st.empty()
        wt_raw_placeholder = st.empty()
        wt_mdld_placeholder = st.empty()
        flow_rate_placeholder = st.empty()
        etf_placeholder = st.empty()

        st.header('Status Lights')
        redpath = Path(__file__).parent.parent / 'LightImages' / 'Red.png'
        yellowpath = Path(__file__).parent.parent / 'LightImages' / 'Yellow.png'
        greenpath = Path(__file__).parent.parent / 'LightImages' / 'Green.png'
        status_images = {
            'Red': str(redpath),
            'Yellow': str(yellowpath),
            'Green': str(greenpath)
        }
        status_light = st.image(str(greenpath), width=IPS.LIGHT_IMG_WIDTH)

        ladle_details_submitted = st.form_submit_button("Submit ladle details")
        if ladle_details_submitted:
            st.success("Ladle Details Submitted")

# Right column with Bucket Schematic and Operator Stats
with right_col:
    st.header('Bucket Schematic')
    init_image =  Path(__file__).parent.parent / 'LadleImages' / 'Ladle_Image_0.png'
    video_gif =  Path(__file__).parent.parent / 'VideoFeed' / 'CameraFeed.gif'
    bucket_image = st.image(str(init_image), width=IPS.LADLE_IMG_WIDTH,
                            caption=f'Fill Level: {int(st.session_state.fill_level)}%')

    st.header('Live Camera Feed')
    operation_feed = st.image(str(video_gif), width=IPS.VIDEO_FEED_WIDTH)

# Initialize the initial sensor reading
initial_sensor_reading = 1

# Start a separate thread to update the Streamlit app
if ladle_details_submitted:
    update_streamlit()
    # Run Streamlit app
    st.write("Streamlit app running...")  # Placeholder to keep the app running
