
import pandas as pd
import json
import os

# Hardcoded date for the report
REPORT_DATE = "2025-12-15"

# Construct the input and output file paths
INPUT_FILE = f"../live_betting/live_betting/orders_log/orders_log_{REPORT_DATE}.csv"
OUTPUT_FILE = f"daily_report_{REPORT_DATE}.csv"

def generate_report():
    """
    Generates a daily report of Kalshi account activity.
    """
    if not os.path.exists(INPUT_FILE):
        print(f"No data found for {REPORT_DATE}")
        return

    # Read the order log data
    df = pd.read_csv(INPUT_FILE)

    # The 'result' column contains a JSON string with the order details.
    # We need to parse this to extract the required information.
    orders = []
    for _, row in df.iterrows():
        try:
            result_data = json.loads(row['result'])
            if 'order' in result_data:
                order_details = result_data['order']
                orders.append({
                    'status': order_details.get('status'),
                    'num_contracts': order_details.get('initial_count'),
                    'num_filled': order_details.get('fill_count'),
                    'ticker': order_details.get('ticker'),
                    'side': order_details.get('side'),
                    'action': order_details.get('action'),
                    'price': order_details.get('yes_price') if order_details.get('side') == 'yes' else order_details.get('no_price'),
                    'created_time': order_details.get('created_time'),
                    'last_update_time': order_details.get('last_update_time'),
                    'client_order_id': order_details.get('client_order_id'),
                })
        except (json.JSONDecodeError, KeyError):
            # This will skip rows where the 'result' column is not a valid JSON string
            # or does not contain the expected 'order' key.
            pass

    if not orders:
        print(f"No order data to process for {REPORT_DATE}")
        return

    # Create a new DataFrame with the extracted order details
    report_df = pd.DataFrame(orders)

    # Reorder columns for the final report
    report_df = report_df[[
        'created_time',
        'last_update_time',
        'ticker',
        'side',
        'action',
        'price',
        'num_contracts',
        'num_filled',
        'status',
        'client_order_id',
    ]]

    # Write the report to a new CSV file
    report_df.to_csv(OUTPUT_FILE, index=False)
    print(f"Daily report generated: {OUTPUT_FILE}")

if __name__ == "__main__":
    generate_report()
