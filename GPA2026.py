import smbus2
import bme280
import time
import json
from datetime import datetime
import board
import adafruit_veml7700
import subprocess
import requests

# --- さくらのAI Engine 設定 ---
SAKURA_API_KEY = "669d4ae7-8eb1-403d-aced-41e6c9d3f137:eB735b6D4pCPQEq8CEexbaLvya03VRew1FqxQXoE"
SAKURA_URL = "https://api.ai.sakura.ad.jp/v1/chat/completions"
# --------------------------------------------------

port = 1
bus = smbus2.SMBus(port)

BME280_ADDR = 0x76
calibration_params = bme280.load_calibration_params(bus, BME280_ADDR)

i2c = board.I2C()
veml = adafruit_veml7700.VEML7700(i2c)

WIND_ADDR = 0x28

try:
    bus.write_byte_data(WIND_ADDR, 0x0B, 0x01)
    print("→ 風速センサーに起動コマンドを送りました")
    time.sleep(0.1)
except Exception as e:
    print("→ 風速センサーの起動でエラーが発生:", e)

print("---洗濯物カンソーク君 ５大センサー＋さくらAIテスト開始（終了は Ctrl+C）---")

# === 💡 AIを前回呼び出したときの基準値（セーブポイント）を保存する変数 ===
last_ai_time = None
last_ai_temp = 0.0
last_ai_hum = 0.0
last_ai_lux = 0.0
last_ai_wind = 0.0
last_ai_press = 0.0
last_ai_rain_osaka = 0
last_ai_rain_gunma = 0

# 前回のAI文章を保持する変数（変化がない時はこれを使い回す）
rain_judge = "計測中..."
laundry_match = "データを読み込み中..."

try:
    while True:
        bme_data = bme280.sample(bus, BME280_ADDR, calibration_params)
        current_temp = round(bme_data.temperature, 1)
        current_hum = round(bme_data.humidity, 1)
        current_press = round(bme_data.pressure, 1)
        
        try:
            current_lux = round(veml.lux, 1)
        except:
            current_lux = 0.0
            
        try:
            bus.v_timeout = 100
            wind_data = bus.read_i2c_block_data(WIND_ADDR, 0x00, 2)
            raw_val = (wind_data[1] << 8) | wind_data[0]
            calculared_wind = round((raw_val / 100.0), 1)

            offset = 2.6
            current_wind = calculared_wind - offset
            if calculared_wind < 0:
                current_wind = 0.0
            else:
                current_wind = round(current_wind, 1)
        except Exception as e:
            current_wind = 0.0 
            
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        url_gunma = "https://www.jma.go.jp/bosai/forecast/data/forecast/100000.json"
        url_osaka = "https://www.jma.go.jp/bosai/forecast/data/forecast/270000.json"
        
        try:
            res_g = requests.get(url_gunma).json()
            current_rain_gunma = int(res_g[0]['timeSeries'][1]['areas'][0]['pops'][0])
            res_o = requests.get(url_osaka).json()
            current_rain_osaka = int(res_o[0]['timeSeries'][1]['areas'][0]['pops'][0])
        except Exception as e:
            current_rain_gunma = 0
            current_rain_osaka = 0
            
        # === 💡 指定された数値で変化判定を行うロジック ===
        need_ai_update = False
        reason = ""
        
        if last_ai_time is None:
            need_ai_update = True
            reason = "初回起動のため"
        else:
            minutes_passed = (now - last_ai_time).total_seconds() / 60.0
            temp_diff = abs(current_temp - last_ai_temp)
            hum_diff = abs(current_hum - last_ai_hum)
            lux_diff = abs(current_lux - last_ai_lux)
            wind_diff = abs(current_wind - last_ai_wind)
            press_diff = abs(current_press - last_ai_press)
            rain_o_diff = abs(current_rain_osaka - last_ai_rain_osaka)
            rain_g_diff = abs(current_rain_gunma - last_ai_rain_gunma)
            
            # 【ご指定の条件チェック】
            if minutes_passed >= 30.0:
                need_ai_update = True
                reason = f"前回から30分経過したため ({round(minutes_passed, 1)}分経過)"
            elif temp_diff >= 2.0:
                need_ai_update = True
                reason = f"気温が {round(temp_diff, 1)}°C 変化したため（基準から±2℃以上）"
            elif hum_diff >= 3.0:
                need_ai_update = True
                reason = f"湿度が {round(hum_diff, 1)}% 変化したため（基準から±3%以上）"
            elif lux_diff >= 10000.0:
                need_ai_update = True
                reason = f"照度が {round(lux_diff, 1)} lx 変化したため（基準から±10000lx以上）"
            elif wind_diff >= 0.5:
                need_ai_update = True
                reason = f"風速が {round(wind_diff, 1)} m/s 変化したため（基準から±0.5m/s以上）"
            elif press_diff >= 1.0:
                need_ai_update = True
                reason = f"気圧が {round(wind_diff, 1)} hPa 変化したため（基準から±1.0hPa以上）"
            elif rain_o_diff >= 10 or rain_g_diff >= 10:
                need_ai_update = True
                reason = "降水確率が 10% 以上変化したため"

        # === 条件に合致した場合のみ AI を実行 ===
        if need_ai_update:
            print(f"🔄 【AI起動】{reason}、さくらAIに新しい文章を要請します。")
            try:
                prompt_content = f"""
                現在の気象データと外部予報をもとに、次の2つの項目について答えてください。
                必ず指定したJSON形式でのみ出力してください。他の説明文は一切不要です。
                
                【現在の状況】
                ・気温: {current_temp}°C
                ・湿度: {current_hum}%
                ・照度: {current_lux} lx
                ・風速: {current_wind} m/s
                ・気圧: {current_press} hPa
                ・大阪の降水確率: {current_rain_osaka}%
                ・群馬の降水確率: {current_rain_gunma}%
                
                【出力形式の指定】
                {{"rain_judge": "雨の予測を、現在の気象データや外部情報をもとに、20~30文字の一文", "laundry_match": "20〜30文字程度のキャッチーな洗濯アドバイス"}}
                """
                
                headers = {
                    "Authorization": f"Bearer {SAKURA_API_KEY}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "gpt-oss-120b",
                    "messages": [
                        {"role": "system", "content": "あなたは優秀な洗濯アドバイザーAIです。必ず指定されたJSONデータのみを返答してください。"},
                        {"role": "user", "content": prompt_content}
                    ],
                    "temperature": 0.0
                }
                
                response = requests.post(SAKURA_URL, headers=headers, json=payload, timeout=15)
                
                if response.status_code == 200:
                    result_json = response.json()
                    ai_text = result_json['choices'][0]['message']['content'].strip()
                    ai_data = json.loads(ai_text)
                    
                    rain_judge = ai_data.get("rain_judge", "判別不能")
                    laundry_match = ai_data.get("laundry_match", "判別不能")
                    
                    # 💡 セーブポイントを現在の値で更新
                    last_ai_time = now
                    last_ai_temp = current_temp
                    last_ai_hum = current_hum
                    last_ai_lux = current_lux
                    last_ai_wind = current_wind
               	    last_ai_press = current_press
                    last_ai_rain_osaka = current_rain_osaka
                    last_ai_rain_gunma = current_rain_gunma
                    print("✅ AI文章とセーブポイントを新しく保存しました。")
                else:
                    print(f"⚠️ さくらAIからエラー返答（ステータスコード: {response.status_code}）")
                    
            except Exception as e:
                print(f"⚠️ AI通信またはパースエラー: {e}")
        else:
            print("💤 【AIスキップ】大きな気象変化がないため、前回のAI文章を再利用して高速化します。")
        # --------------------------------------------------
        
        weather_data = {
            "temperature": current_temp,
            "humidity": current_hum,
            "pressure": current_press,
            "lux": current_lux,
            "wind_speed": current_wind,
            "rain_judge": rain_judge,      
            "laundry_match": laundry_match,  
            "last_update": now_str
        }
        
        with open("/home/pi/Desktop/GPA2026/data.json", "w", encoding="utf-8") as f:
            json.dump(weather_data, f, indent=4, ensure_ascii=False)
            
        subprocess.run(["git", "add", "data.json"], cwd="/home/pi/Desktop/GPA2026")
        subprocess.run(["git", "commit", "-m", "Auto Update"], cwd="/home/pi/Desktop/GPA2026")
        subprocess.run(["git", "push", "origin", "main"], cwd="/home/pi/Desktop/GPA2026")
            
        print(f"[{now_str}]  5大データ＋AIダブル診断を GitHub に送信しました")
        print(f" 温:{weather_data['temperature']}°C | 湿:{weather_data['humidity']}% | 圧:{weather_data['pressure']}hPa")
        print(f" 明:{weather_data['lux']}lx | 風:{weather_data['wind_speed']}m/s")
        print(f" AI雨予測: {weather_data['rain_judge']}")
        print(f" AI判定: {weather_data['laundry_match']}")
        print("-----------------------------------------")
        
        time.sleep(60)
            
except KeyboardInterrupt:
    print("\nテストを終了しました。")
