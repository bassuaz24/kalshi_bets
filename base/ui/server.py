"""
Web-based UI server for starting/stopping algorithm and viewing performance.
"""

import threading
import time
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import uvicorn

from config import settings
from app import state
from app.loop import main as run_main_loop
from positions.metrics import get_position_summary
from bot_logging.daily_reports import generate_daily_report

app = FastAPI(
    title="Trading Bot UI",
    description="Web interface for trading bot control and monitoring",
    version="1.0.0"
)

# Global thread for main loop
_main_loop_thread: Optional[threading.Thread] = None


def start_algorithm():
    """Start the trading algorithm in a separate thread."""
    global _main_loop_thread
    
    if state.algorithm_running:
        return {"status": "already_running", "message": "Algorithm is already running"}
    
    if _main_loop_thread and _main_loop_thread.is_alive():
        return {"status": "already_running", "message": "Algorithm thread is already running"}
    
    state.algorithm_running = True
    _main_loop_thread = threading.Thread(target=run_main_loop, daemon=True)
    _main_loop_thread.start()
    
    return {"status": "started", "message": "Algorithm started successfully"}


def stop_algorithm():
    """Stop the trading algorithm."""
    global _main_loop_thread
    
    if not state.algorithm_running:
        return {"status": "not_running", "message": "Algorithm is not running"}
    
    state.algorithm_running = False
    
    # Wait for thread to finish (with timeout)
    if _main_loop_thread and _main_loop_thread.is_alive():
        _main_loop_thread.join(timeout=10.0)
    
    return {"status": "stopped", "message": "Algorithm stopped successfully"}


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the main UI page."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trading Bot Control Panel</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .header {
                background-color: #2c3e50;
                color: white;
                padding: 20px;
                border-radius: 5px;
                margin-bottom: 20px;
            }
            .control-panel {
                background-color: white;
                padding: 20px;
                border-radius: 5px;
                margin-bottom: 20px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .status-panel {
                background-color: white;
                padding: 20px;
                border-radius: 5px;
                margin-bottom: 20px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            button {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 5px;
                cursor: pointer;
                font-size: 16px;
                margin-right: 10px;
            }
            button:hover {
                background-color: #2980b9;
            }
            button.stop {
                background-color: #e74c3c;
            }
            button.stop:hover {
                background-color: #c0392b;
            }
            .status {
                padding: 10px;
                border-radius: 5px;
                margin: 10px 0;
            }
            .status.running {
                background-color: #d4edda;
                color: #155724;
            }
            .status.stopped {
                background-color: #f8d7da;
                color: #721c24;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 20px;
            }
            th, td {
                padding: 10px;
                text-align: left;
                border-bottom: 1px solid #ddd;
            }
            th {
                background-color: #3498db;
                color: white;
            }
            .metric {
                display: inline-block;
                margin: 10px;
                padding: 15px;
                background-color: #ecf0f1;
                border-radius: 5px;
                min-width: 150px;
            }
            .metric-value {
                font-size: 24px;
                font-weight: bold;
                color: #2c3e50;
            }
            .metric-label {
                font-size: 14px;
                color: #7f8c8d;
                margin-top: 5px;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🚀 Trading Bot Control Panel</h1>
        </div>
        
        <div class="control-panel">
            <h2>Control</h2>
            <button id="startBtn" onclick="startAlgorithm()">Start Algorithm</button>
            <button id="stopBtn" class="stop" onclick="stopAlgorithm()">Stop Algorithm</button>
            <button onclick="refreshStatus()">Refresh Status</button>
            <div id="controlStatus" class="status stopped">Status: Stopped</div>
        </div>
        
        <div class="status-panel">
            <h2>Performance Metrics</h2>
            <div id="metrics"></div>
        </div>
        
        <div class="status-panel">
            <h2>Positions</h2>
            <div id="positions"></div>
        </div>
        
        <script>
            function startAlgorithm() {
                fetch('/api/start', {method: 'POST'})
                    .then(r => r.json())
                    .then(data => {
                        alert(data.message);
                        refreshStatus();
                    })
                    .catch(e => alert('Error: ' + e));
            }
            
            function stopAlgorithm() {
                fetch('/api/stop', {method: 'POST'})
                    .then(r => r.json())
                    .then(data => {
                        alert(data.message);
                        refreshStatus();
                    })
                    .catch(e => alert('Error: ' + e));
            }
            
            function refreshStatus() {
                fetch('/api/status')
                    .then(r => r.json())
                    .then(data => {
                        const statusDiv = document.getElementById('controlStatus');
                        statusDiv.className = 'status ' + (data.running ? 'running' : 'stopped');
                        statusDiv.textContent = 'Status: ' + (data.running ? 'Running' : 'Stopped');
                        
                        // Update metrics
                        const metricsDiv = document.getElementById('metrics');
                        metricsDiv.innerHTML = `
                            <div class="metric">
                                <div class="metric-value">$${data.summary.total_pnl.toFixed(2)}</div>
                                <div class="metric-label">Total PnL</div>
                            </div>
                            <div class="metric">
                                <div class="metric-value">$${data.summary.realized_pnl.toFixed(2)}</div>
                                <div class="metric-label">Realized PnL</div>
                            </div>
                            <div class="metric">
                                <div class="metric-value">$${data.summary.unrealized_pnl.toFixed(2)}</div>
                                <div class="metric-label">Unrealized PnL</div>
                            </div>
                            <div class="metric">
                                <div class="metric-value">${data.summary.total_positions}</div>
                                <div class="metric-label">Open Positions</div>
                            </div>
                            <div class="metric">
                                <div class="metric-value">${data.summary.wins}</div>
                                <div class="metric-label">Wins</div>
                            </div>
                            <div class="metric">
                                <div class="metric-value">${data.summary.losses}</div>
                                <div class="metric-label">Losses</div>
                            </div>
                        `;
                        
                        // Update positions
                        const positionsDiv = document.getElementById('positions');
                        if (data.positions.length === 0) {
                            positionsDiv.innerHTML = '<p>No open positions</p>';
                        } else {
                            let html = '<table><tr><th>Market</th><th>Side</th><th>Stake</th><th>Entry Price</th><th>Unrealized PnL</th></tr>';
                            data.positions.forEach(pos => {
                                html += `<tr>
                                    <td>${pos.market_ticker}</td>
                                    <td>${pos.side.toUpperCase()}</td>
                                    <td>${pos.stake}</td>
                                    <td>${(pos.entry_price * 100).toFixed(2)}%</td>
                                    <td>$${pos.unrealized_pnl.toFixed(2)}</td>
                                </tr>`;
                            });
                            html += '</table>';
                            positionsDiv.innerHTML = html;
                        }
                    })
                    .catch(e => console.error('Error:', e));
            }
            
            // Auto-refresh every 5 seconds
            setInterval(refreshStatus, 5000);
            refreshStatus();
        </script>
    </body>
    </html>
    """
    return html_content


@app.post("/api/start")
def api_start():
    """API endpoint to start the algorithm."""
    result = start_algorithm()
    return JSONResponse(content=result)


@app.post("/api/stop")
def api_stop():
    """API endpoint to stop the algorithm."""
    result = stop_algorithm()
    return JSONResponse(content=result)


@app.get("/api/status")
def api_status():
    """API endpoint to get current status and performance."""
    summary = get_position_summary()
    
    # Get positions with unrealized PnL
    positions_data = []
    for p in state.positions:
        if p.get("settled", False):
            continue
        from positions.metrics import get_position_unrealized_pnl
        unrealized_pnl = get_position_unrealized_pnl(p)
        positions_data.append({
            "market_ticker": p.get("market_ticker", ""),
            "side": p.get("side", ""),
            "stake": p.get("stake", 0),
            "entry_price": p.get("entry_price", 0.0),
            "unrealized_pnl": unrealized_pnl,
        })
    
    return JSONResponse(content={
        "running": state.algorithm_running,
        "summary": summary,
        "positions": positions_data,
    })


@app.get("/api/positions")
def api_positions():
    """API endpoint to get detailed positions."""
    positions_data = []
    for p in state.positions:
        if p.get("settled", False):
            continue
        from positions.metrics import get_position_unrealized_pnl
        unrealized_pnl = get_position_unrealized_pnl(p)
        positions_data.append({
            "match": p.get("match", ""),
            "market_ticker": p.get("market_ticker", ""),
            "event_ticker": p.get("event_ticker", ""),
            "side": p.get("side", ""),
            "stake": p.get("stake", 0),
            "entry_price": p.get("entry_price", 0.0),
            "entry_time": p.get("entry_time", ""),
            "stop_loss": p.get("stop_loss"),
            "take_profit": p.get("take_profit"),
            "unrealized_pnl": unrealized_pnl,
        })
    return JSONResponse(content={"positions": positions_data})


@app.get("/api/metrics")
def api_metrics():
    """API endpoint to get performance metrics."""
    summary = get_position_summary()
    return JSONResponse(content={
        "summary": summary,
        "metrics": state.METRICS,
    })


@app.post("/api/report/generate")
def api_generate_report():
    """API endpoint to generate daily report."""
    try:
        report = generate_daily_report()
        return JSONResponse(content={"status": "success", "report": report})
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


def start_ui_server(port: int = None, host: str = None):
    """Start the UI server in a separate thread.
    
    Args:
        port: Port to run the server on (default from settings)
        host: Host to bind to (default from settings)
    """
    port = port or settings.UI_PORT
    host = host or settings.UI_HOST
    
    def run_server():
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
        )
        server = uvicorn.Server(config)
        server.run()
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    print(f"🌐 UI server started on http://{host}:{port}")
    return thread


if __name__ == "__main__":
    # Start UI server
    start_ui_server()
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 UI server stopped")