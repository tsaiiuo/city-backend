from flask import Flask, request, jsonify
import mysql.connector
from flask_cors import CORS
from datetime import datetime
from datetime import datetime, timedelta
import pytz
from collections import defaultdict
from dotenv import load_dotenv
#---------------------------------
import pandas as pd
import os
import numpy as np
import requests

#---------------------------------

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
load_dotenv()
# MySQL 資料庫配置 替換MySQL 資訊 
db_config = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "cityproject")
}
print(os.getenv("MYSQL_HOST"))
# 定義台灣時區
taiwan_tz = pytz.timezone("Asia/Taipei")
def get_current_taiwan_time():
    utc_now = datetime.utcnow().replace(tzinfo=pytz.utc)
    taiwan_now = utc_now.astimezone(taiwan_tz)
    return taiwan_now.strftime("%Y-%m-%d %H")
# 定義全域變數
HOURS_PER_SHIFT = 1
# time_slots = [f"{hour}" for hour in range(8, 18)] + [f"{hour}:30" for hour in range(8, 18)] # 每小時從 7:00 到 17:00
time_slots = [f"{hour}:00" for hour in range(8, 16)]

def get_current_taiwan_time():
    utc_now = datetime.utcnow().replace(tzinfo=pytz.utc)
    taiwan_now = utc_now.astimezone(taiwan_tz)

    # 獲取當前時間的時和分
    hour = taiwan_now.hour
    minute = taiwan_now.minute

    # 計算進位後的時間
    if minute < 30:
        # minute = 30
        minute = 0
    else:
        minute = 0
        hour += 1  # 進位到下一個小時

    # 處理進位導致跨天的情況
    if hour == 24:
        hour = 0
        taiwan_now += timedelta(days=1)

    # 再加五天
    taiwan_now += timedelta(days=5)
    # 格式化時間
    return taiwan_now.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
print(get_current_taiwan_time())
# 判斷是否為假日（週六或週日）
def is_weekend(date):
    weekday = date.weekday()  # 0 是星期一，6 是星期日
    return weekday in (5, 6)  # 5 是星期六，6 是星期日

# 跳到下一個工作日（星期一）
def next_workday(date):
    while is_weekend(date):  # 只要是週末就繼續加一天
        date += timedelta(days=1)
    return date

# MySQL 查詢執行器
def execute_query(query, params=None, fetch=True):
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor(dictionary=True)
    cursor.execute(query, params or ())
    if fetch:
        results = cursor.fetchall()
    else:
        results = None
    connection.commit()
    cursor.close()
    connection.close()
    return results
# 從資料庫中獲取員工數據
def get_employees_from_db():
    query = "SELECT employee_id, name, work_hours, work FROM employees"
    return execute_query(query)
# 移動到下一個時間段
def next_time_slot(current_time_slot):
    date, time_slot = current_time_slot.split()
    try:
        next_slot_index = time_slots.index(time_slot) + 1
        if next_slot_index < len(time_slots):
            return f"{date} {time_slots[next_slot_index]}"
        else:
            # 當前時間段已到一天的結尾，移動到下一天
            next_date = datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)
            next_date = next_workday(next_date)  # 跳過週末
            return f"{next_date.strftime('%Y-%m-%d')} {time_slots[0]}"
    except ValueError:
        # 如果 time_slot 不在 time_slots 中，直接跳到隔天的起點，並跳過週末
        next_date = datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)
        next_date = next_workday(next_date)  # 跳過週末
        return f"{next_date.strftime('%Y-%m-%d')} {time_slots[0]}"

def assign_task(task_id, office_id, required_hours):
    current_time_slot = get_current_taiwan_time()  # 獲取當前時間的台灣時區格式
    required_shifts = required_hours // HOURS_PER_SHIFT

    # 從資料庫獲取員工列表
    query = "SELECT employee_id, name, work_hours, work, office_id FROM employees WHERE office_id = %s"
    employees = execute_query(query, (office_id,))

    def get_schedule_for_employee(employee_id):
        """
        根據 employee_id 從 schedule 表中獲取員工的所有排班時間段
        """
        query = """
        SELECT start_time, end_time FROM schedule WHERE employee_id = %s
        UNION ALL
        SELECT start_time, end_time FROM leave_records WHERE employee_id = %s
        UNION ALL
        SELECT start_time, end_time FROM divide_records WHERE employee_id = %s
        """        
        return execute_query(query, (employee_id,employee_id,employee_id))

    def is_time_slot_available(employee_id, time_slot):
        """
        檢查指定的員工在某個時間段是否可用
        """
        schedules = get_schedule_for_employee(employee_id)
        for schedule in schedules:

            start_time = datetime.strptime(schedule["start_time"].rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f")
            start_time=start_time.replace(tzinfo=pytz.utc)
            start_time=start_time.astimezone(taiwan_tz)
            end_time = datetime.strptime(schedule["end_time"].rstrip("Z"), "%Y-%m-%dT%H:%M:%S.%f")
            end_time=end_time.replace(tzinfo=pytz.utc)
            end_time=end_time.astimezone(taiwan_tz)
            current_time = datetime.strptime(time_slot, "%Y-%m-%d %H:%M")
            current_time = taiwan_tz.localize(current_time)

           
            # print(start_time <= current_time <= end_time)
            if start_time <= current_time < end_time:
                return False
        return True

    def generate_slots(employee_id, start_time_slot, required_shifts):
        """
        根據員工 ID 和開始時間段生成排班時間段，跳過有衝突的時間
        """
        slots = []
        temp_time_slot = start_time_slot
        date, time_slot = temp_time_slot.split()

        if time_slot not in time_slots:

            temp_time_slot = next_time_slot(temp_time_slot)
        while len(slots) < required_shifts:
            if is_time_slot_available(employee_id, temp_time_slot):
                slots.append(temp_time_slot)
            temp_time_slot = next_time_slot(temp_time_slot)
        return slots

    # 選擇可用員工，排除在當前時間段有衝突的員工
    available_employees = sorted(
        [
            emp for emp in employees
            if emp["work"] == 1 
        ],
        key=lambda emp: emp["work_hours"]
    )
    if required_hours == 0 :
        return {
            "task_id": task_id,
            "best_assignment": {
                "assigned_employee": "無法判別地號請自行選擇員工",
                "assigned_slots": "",
                "start_time": "",
                "end_time": "",
                "required_hours": "無法判別地號"
            }}
    if len(available_employees) >= 2:  # 確保有足夠的員工可選
        best_employee = available_employees[0]
        second_best_employee = available_employees[1]

        # 為最佳員工生成排班
        best_assigned_slots = generate_slots(best_employee["employee_id"], current_time_slot, required_shifts)
        best_start_time = best_assigned_slots[0]
        best_end_time = best_assigned_slots[-1]

        # 為次佳員工生成排班
        second_assigned_slots = generate_slots(second_best_employee["employee_id"], current_time_slot, required_shifts)
        second_start_time = second_assigned_slots[0]
        second_end_time = second_assigned_slots[-1]
        print(best_employee["name"])
        return {
            "task_id": task_id,
            "best_assignment": {
                "assigned_employee": best_employee["name"],
                "assigned_slots": best_assigned_slots,
                "start_time": best_start_time,
                "end_time": best_end_time,
                "required_hours": required_hours
            },
            "second_best_assignment": {
                "assigned_employee": second_best_employee["name"],
                "assigned_slots": second_assigned_slots,
                "start_time": second_start_time,
                "end_time": second_end_time,
                "required_hours": required_hours
            }
        }
    elif len(available_employees) == 1:  # 僅有一名可用員工
        best_employee = available_employees[0]

        # 為最佳員工生成排班
        best_assigned_slots = generate_slots(best_employee["employee_id"], current_time_slot, required_shifts)
        best_start_time = best_assigned_slots[0]
        best_end_time = best_assigned_slots[-1]
        print(best_employee["name"])
        return {
            "task_id": task_id,
            "best_assignment": {
                "assigned_employee": best_employee["name"],
                "assigned_slots": best_assigned_slots,
                "start_time": best_start_time,
                "end_time": best_end_time,
                "required_hours": required_hours
            },
            "second_best_assignment": None  # 無次佳分配
        }
    else:
        print(123)
        return {
            "task_id": task_id,
            "best_assignment": {
                "assigned_employee": "無法判別地號請自行選擇員工",
                "assigned_slots": "",
                "start_time": "",
                "end_time": "",
                "required_hours": "無員工可排班"
            }}





@app.route("/tasks", methods=["POST"])
def add_task():
    data = request.json
    fields = [
        "office_name","office_id", "land_section", "local_point", "stake_point",
        "work_area", "check_time", "diagramornumeric", "cadastral_arrangement"
    ]
    values = [data.get(field) for field in fields]

    if None in values:
        return jsonify({"error": "Missing required fields"}), 400

    query = f"""
        INSERT INTO tasks ({', '.join(fields)})
        VALUES ({', '.join(['%s'] * len(fields))})
    """

    # 插入數據並返回 task_id
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor()
    try:
        cursor.execute(query, values)
        connection.commit()
        task_id = cursor.lastrowid  # 獲取自動生成的 task_id
    except mysql.connector.Error as e:
        connection.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    finally:
        cursor.close()
        connection.close()

    return jsonify({"message": "Data inserted successfully", "task_id": task_id}), 201


# GET /tasks - 查詢任務
@app.route("/tasks", methods=["GET"])
def get_tasks():
    is_scheduled = request.args.get("is_scheduled")
    office_id = request.args.get("office_id")
    query = "SELECT * FROM tasks"
    params = []
    conditions = []

    if is_scheduled:
        conditions.append("is_scheduled = %s")
        params.append(is_scheduled)

    if office_id:
        conditions.append("office_id = %s")
        params.append(office_id)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    results = execute_query(query, params)
    return jsonify(results), 200
@app.route("/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    data = request.get_json()
    # 允許更新的欄位
    allowed_fields = [
        "office_name", "land_section", "local_point", "stake_point",
        "work_area", "check_time", "diagramornumeric", "cadastral_arrangement"
    ]
    update_fields = []
    params = []
    for field in allowed_fields:
        if field in data:
            update_fields.append(f"{field} = %s")
            params.append(data[field])
    if not update_fields:
        return jsonify({"error": "No fields to update"}), 400

    params.append(task_id)
    query = f"UPDATE tasks SET {', '.join(update_fields)} WHERE task_id = %s"

    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor()
    try:
        cursor.execute(query, params)
        connection.commit()
    except mysql.connector.Error as e:
        connection.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    finally:
        cursor.close()
        connection.close()

    return jsonify({"message": "Task updated successfully"}), 200

@app.route('/employees/<int:employee_id>/work', methods=['PUT'])
def update_employee_work(employee_id):
    """
    更新指定員工 (employee_id) 的 work 欄位。
    前端應透過 JSON 傳入:
      {
         "work": 0 或 1
      }
    """
    data = request.get_json()
    if not data or 'work' not in data:
        return jsonify({"error": "請提供 work 欄位的值"}), 400

    work = data.get('work')
    # 可加入驗證，確認 work 值是否為 0 或 1
    if work not in [0, 1]:
        return jsonify({"error": "work 欄位值必須是 0 或 1"}), 400

    query = "UPDATE employees SET work = %s WHERE employee_id = %s"
    try:
        execute_query(query, (work, employee_id), fetch=False)
        return jsonify({"message": "員工工作狀態更新成功"}), 200
    except Exception as e:
        return jsonify({"error": f"資料庫錯誤: {str(e)}"}), 500

# Complete Task API
@app.route("/tasks/complete/<int:task_id>", methods=["PUT"])
def complete_task(task_id):
    """
    將指定 task_id 的任務標記為已完結，設定 is_scheduled 為 1，
    並計算該任務已完成的總工時，將 (task_id, total_time) 插入 record 表中。
    """
    # 更新 tasks 表
    query = "UPDATE tasks SET is_scheduled = 1 WHERE task_id = %s"
    try:
        execute_query(query, [task_id], fetch=False)
    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500

    # 由前端傳入的 JSON 解析 current_time
    try:
        data = request.get_json()
        # 取得前端傳入的 current_time 字串
        current_time_str = data.get("current_time")
        if current_time_str:
            # 使用 dateutil.parser 解析前端傳入的時間字串
            from dateutil import parser
            current_time = parser.parse(current_time_str)
            # 將時間轉換到台灣時區（若原字串有 offset，這裡會轉換成 taiwan_tz 的時間）
            current_time = current_time.astimezone(taiwan_tz)
        else:
            # 若前端未提供，則以當前台灣時間為準
            current_time = datetime.now(taiwan_tz)
    except Exception as e:
        return jsonify({"error": f"Error parsing current_time: {str(e)}"}), 400

    total_time = 0.0
    try:
        # 撈取該 task_id 的所有排班記錄
        schedule_query = "SELECT start_time, end_time FROM schedule WHERE task_id = %s"
        schedules = execute_query(schedule_query, [task_id])
        
        from math import ceil

        for sched in schedules:
            # 將 ISO 格式的時間字串轉換成 datetime 物件（台灣時區）
            start_str = sched["start_time"].rstrip("Z")
            end_str = sched["end_time"].rstrip("Z")
            start_dt = datetime.strptime(start_str, "%Y-%m-%dT%H:%M:%S.%f")
            start_dt = start_dt.replace(tzinfo=pytz.utc).astimezone(taiwan_tz)
            end_dt = datetime.strptime(end_str, "%Y-%m-%dT%H:%M:%S.%f")
            end_dt = end_dt.replace(tzinfo=pytz.utc).astimezone(taiwan_tz)

            # 若排班已完全結束，則計算完整時數
            if end_dt < current_time:
                diff_hours = (end_dt - start_dt).total_seconds() / 3600.0
                total_time += diff_hours
            # 若目前處於排班進行中，則計算已進行時數（無條件進位小時）
            elif start_dt < current_time < end_dt:
                diff_hours = (current_time - start_dt).total_seconds() / 3600.0
                total_time += ceil(diff_hours)
            # 若排班尚未開始，則不計算

        # 插入計算結果到 record 表（create_at 由資料庫預設產生）
        insert_query = "INSERT INTO record (task_id, total_time) VALUES (%s, %s)"
        execute_query(insert_query, [task_id, total_time], fetch=False)
    except Exception as e:
        return jsonify({"error": f"Error calculating total time or inserting record: {str(e)}"}), 500

    return jsonify({
        "message": "Task marked as complete successfully and record inserted",
        "total_time": total_time
    }), 200

# Delete Task API
@app.route("/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    """
    從 tasks 表中刪除指定 task_id 的任務
    """
    query = "DELETE FROM tasks WHERE task_id = %s"
    try:
        execute_query(query, [task_id], fetch=False)
    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    return jsonify({"message": "Task deleted successfully"}), 200
# POST /schedule - 新增排班
@app.route("/schedule", methods=["POST"])
def add_schedule():
    data = request.json
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    employee_id = data.get("employee_id")
    task_id = data.get("task_id")
    name = data.get("name")

    if not (start_time and end_time and (employee_id or name)):
        return jsonify({"error": "Missing required fields"}), 400

    if not employee_id and name:
        query = "SELECT employee_id FROM employees WHERE name = %s "
        result = execute_query(query, [name])
        if not result:
            return jsonify({"error": "Employee not found"}), 404
        employee_id = result[0]["employee_id"]
    if(end_time<start_time):
        return jsonify({"error": "End Time should after the Start Time"}), 400
    # 檢查排班時間是否重疊：
    # 假設排班時間皆採用 ISO 格式存放，直接以字串比較也可，或轉為 DATETIME 比較更精確
    conflict_query = """
        SELECT * FROM schedule 
        WHERE employee_id = %s 
          AND (start_time < %s AND end_time > %s)
    """
    conflict = execute_query(conflict_query, [employee_id, end_time, start_time])
    if conflict and len(conflict) > 0:
        return jsonify({"error": "Schedule conflict exists for the given time interval."}), 400

    insert_query = """
        INSERT INTO schedule (start_time, end_time, employee_id, task_id)
        VALUES (%s, %s, %s, %s)
    """
    execute_query(insert_query, [start_time, end_time, employee_id, task_id], fetch=False)

    # 更新員工總工時
    start = datetime.fromisoformat(start_time.rstrip("Z"))
    end = datetime.fromisoformat(end_time.rstrip("Z"))
    hours_diff = (end - start).total_seconds() / 3600
    update_query = "UPDATE employees SET work_hours = work_hours + %s WHERE employee_id = %s"
    execute_query(update_query, [hours_diff, employee_id], fetch=False)

    # 更新月工時
    schedule_dt = datetime.fromisoformat(start_time.rstrip("Z"))
    year = schedule_dt.year
    month = schedule_dt.month
    select_query = "SELECT * FROM monthly_work_hours WHERE employee_id = %s AND year = %s AND month = %s"
    monthly_records = execute_query(select_query, [employee_id, year, month])
    if monthly_records and len(monthly_records) > 0:
        update_monthly_query = "UPDATE monthly_work_hours SET work_hours = work_hours + %s WHERE employee_id = %s AND year = %s AND month = %s"
        execute_query(update_monthly_query, [hours_diff, employee_id, year, month], fetch=False)
    else:
        insert_monthly_query = "INSERT INTO monthly_work_hours (employee_id, year, month, work_hours) VALUES (%s, %s, %s, %s)"
        execute_query(insert_monthly_query, [employee_id, year, month, hours_diff], fetch=False)

    return jsonify({"message": "Schedule inserted successfully"}), 201

# GET /schedule - 查詢排班
@app.route("/schedule", methods=["GET"])
def get_schedule():
    start_time = request.args.get("start_time")
    end_time = request.args.get("end_time")
    employee_id = request.args.get("employee_id")
    task_id = request.args.get("task_id")
    office_id = request.args.get("office_id")

    query = """
        SELECT
            tasks.task_id AS id,
            tasks.is_scheduled AS is_scheduled,
            schedule.schedule_id AS schedule_id,
            schedule.start_time AS start,
            schedule.end_time AS end,
            schedule.employee_id,
            schedule.task_id,
            employees.name AS name,
            tasks.check_time AS check_time,
            tasks.land_section AS land_section,
            tasks.local_point AS local_point,
            tasks.stake_point AS stake_point,
            tasks.work_area AS work_area
        FROM schedule
        JOIN employees ON schedule.employee_id = employees.employee_id
        JOIN tasks ON schedule.task_id = tasks.task_id
    """

    params = []
    conditions = []

    if start_time:
        conditions.append("schedule.start_time >= %s")
        params.append(start_time)

    if end_time:
        conditions.append("schedule.end_time <= %s")
        params.append(end_time)

    if employee_id:
        conditions.append("schedule.employee_id = %s")
        params.append(employee_id)

    if task_id:
        conditions.append("schedule.task_id = %s")
        params.append(task_id)

    if office_id:
        conditions.append("employees.office_id = %s")
        params.append(office_id)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    results = execute_query(query, params)

    print(jsonify(results))
    return jsonify(results), 200

# DELETE /schedule - 刪除排班
@app.route("/schedule", methods=["DELETE"])
def delete_schedule():
    data = request.json
    schedule_id = data.get("schedule_id")
    employee_id = data.get("employee_id")
    start_time = data.get("start_time")
    end_time = data.get("end_time")

    if not (schedule_id and employee_id and start_time and end_time):
        return jsonify({"error": "Missing required fields"}), 400

    # 刪除排班
    delete_query = "DELETE FROM schedule WHERE schedule_id = %s"
    execute_query(delete_query, [schedule_id], fetch=False)

    # 更新員工工作時長
    start = datetime.fromisoformat(start_time.rstrip("Z"))
    end = datetime.fromisoformat(end_time.rstrip("Z"))
    hours_diff = (end - start).total_seconds() / 3600.0
    print("Hours difference:", hours_diff)
    
    # 使用 GREATEST 保證 work_hours 不會小於 0
    update_emp_query = "UPDATE employees SET work_hours = GREATEST(work_hours - %s, 0) WHERE employee_id = %s"
    execute_query(update_emp_query, [hours_diff, employee_id], fetch=False)

    # 更新月工時：以排班開始時間所在的年份與月份為依據
    schedule_dt = datetime.fromisoformat(start_time.rstrip("Z"))
    year = schedule_dt.year
    month = schedule_dt.month
    update_monthly_query = """
        UPDATE monthly_work_hours 
        SET work_hours = GREATEST(work_hours - %s, 0)
        WHERE employee_id = %s AND year = %s AND month = %s
    """
    execute_query(update_monthly_query, [hours_diff, employee_id, year, month], fetch=False)

    return jsonify({"message": "Schedule deleted successfully"}), 200


@app.route("/monthly_work_hours", methods=["GET"])
def get_monthly_work_hours():
    # 取得 month 參數（必填）
    month = request.args.get("month")
    # 若未提供 year，預設為目前台灣時區的年份
    year = request.args.get("year") or datetime.now(taiwan_tz).year
    office_id = request.args.get("office_id")
    if not month:
        return jsonify({"error": "Missing required parameter: month"}), 400

    query = """
        SELECT e.employee_id, e.name, SUM(m.work_hours) AS total_hours
        FROM monthly_work_hours m
        JOIN employees e ON m.employee_id = e.employee_id
        WHERE m.year = %s AND m.month = %s AND e.office_id = %s
        GROUP BY e.employee_id, e.name, e.office_id
    """
    result = execute_query(query, [year, month,office_id])
    return jsonify(result), 200
@app.route("/tasks/yearly_counts", methods=["GET"])
def get_yearly_tasks_counts():
    """
    根據 tasks 表的 created_at 欄位，統計指定年份每個月份的事件數量。
    若某個月份無資料，則回傳 count 為 0。
    查詢參數:
      - year (可選): 年份，若未提供則預設使用目前台灣時區的年份
      - office_id (可選): 過濾指定事務所的資料
    回傳格式: { "year": <year>, "monthly_counts": [{"month": 1, "count": ...}, ...] }
    """
    year = request.args.get("year") or datetime.now(taiwan_tz).year
    office_id = request.args.get("office_id")

    query = """
        SELECT MONTH(created_at) AS month, COUNT(*) AS count
        FROM tasks
        WHERE YEAR(created_at) = %s
    """
    params = [year]

    if office_id:
        query += " AND office_id = %s"
        params.append(office_id)

    query += " GROUP BY MONTH(created_at)"
    result = execute_query(query, params)

    # 建立1~12月份的預設結果
    monthly_counts = {m: 0 for m in range(1, 13)}
    for row in result:
        monthly_counts[row["month"]] = row["count"]

    monthly_counts_list = [{"month": m, "count": monthly_counts[m]} for m in range(1, 13)]

    return jsonify({"year": year, "monthly_counts": monthly_counts_list}), 200
@app.route("/tasks/land_section_stats", methods=["GET"])
def get_land_section_stats():
    """
    根據 tasks 表的 created_at 欄位，統計指定月份的各地段號 (land_section) 筆數。
    查詢參數:
      - month (必填): 月份 (1 ~ 12)
      - year (可選): 年份，若未提供則預設使用目前台灣時區的年份
      - office_id (可選): 過濾指定事務所的資料
    回傳格式:
      {
         "year": <year>,
         "month": <month>,
         "land_section_stats": [
             {"land_section": "536", "count": 2},
             {"land_section": "XXX", "count": Y},
             ...
         ]
      }
    """
    month = request.args.get("month")
    if not month:
        return jsonify({"error": "Missing required parameter: month"}), 400

    year = request.args.get("year") or datetime.now(taiwan_tz).year
    office_id = request.args.get("office_id")

    query = """
        SELECT land_section, COUNT(*) AS count
        FROM tasks
        WHERE YEAR(created_at) = %s AND MONTH(created_at) = %s
    """
    params = [year, month]

    if office_id:
        query += " AND office_id = %s"
        params.append(office_id)

    query += " GROUP BY land_section"
    result = execute_query(query, params)
    return jsonify({"year": year, "month": month, "land_section_stats": result}), 200

@app.route("/assign_task", methods=["POST"])
def assign_task_api():
    data = request.json
    task_id = data.get("task_id")
    required_hours = data.get("required_hours")
    office_id = data.get("office_id")

    if not task_id :
        return jsonify({"error": "Missing required fields"}), 400

    result = assign_task(task_id, office_id, required_hours)
    return jsonify(result)

@app.route("/leave_records", methods=["GET"])
def get_leave_records():
    try:
        office_id = request.args.get("office_id")
        query = """
            SELECT 
                leave_id, 
                leave_records.employee_id AS employee_id, 
                start_time AS start, 
                end_time AS end, 
                leave_type, 
                reason, 
                created_at, 
                employees.name AS name
            FROM leave_records
            JOIN employees ON leave_records.employee_id = employees.employee_id
        """
        params = []
        if office_id:
            query += " WHERE employees.office_id = %s"
            params.append(office_id)

        records = execute_query(query, params)
        return jsonify(records), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
# 新增請假記錄
@app.route("/leave_records", methods=["POST"])
def add_leave_record():
    data = request.get_json()
    # 必填欄位： employee_id, start_time, end_time, leave_type
    required_fields = ["employee_id", "start_time", "end_time", "leave_type"]
    if not data or any(field not in data for field in required_fields):
        return jsonify({"error": "Missing required field(s)"}), 400

    employee_id = data.get("employee_id")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    leave_type = data.get("leave_type")
    reason = data.get("reason")  # 可選欄位

    query = """
        INSERT INTO leave_records (employee_id, start_time, end_time, leave_type, reason)
        VALUES (%s, %s, %s, %s, %s)
    """
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor()
    try:
        cursor.execute(query, (employee_id, start_time, end_time, leave_type, reason))
        connection.commit()
        leave_id = cursor.lastrowid
    except mysql.connector.Error as e:
        connection.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    finally:
        cursor.close()
        connection.close()

    new_record = {
        "leave_id": leave_id,
        "employee_id": employee_id,
        "start_time": start_time,
        "end_time": end_time,
        "leave_type": leave_type,
        "reason": reason,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    return jsonify(new_record), 201

# 刪除請假記錄（依 leave_id 刪除）
@app.route("/leave_records/<int:leave_id>", methods=["DELETE"])
def delete_leave_record(leave_id):
    query = "DELETE FROM leave_records WHERE leave_id = %s"
    try:
        execute_query(query, (leave_id,), fetch=False)
        return jsonify({"message": "Leave record deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 更新請假記錄（依 leave_id 更新）
@app.route("/leave_records/<int:leave_id>", methods=["PUT"])
def update_leave_record(leave_id):
    data = request.get_json()
    # 允許更新的欄位
    updatable_fields = ["employee_id", "start_time", "end_time", "leave_type", "reason"]
    update_fragments = []
    params = []
    for field in updatable_fields:
        if field in data:
            update_fragments.append(f"{field} = %s")
            params.append(data[field])
    if not update_fragments:
        return jsonify({"error": "No fields to update"}), 400
    params.append(leave_id)
    query = f"UPDATE leave_records SET {', '.join(update_fragments)} WHERE leave_id = %s"
    try:
        execute_query(query, params, fetch=False)
        return jsonify({"message": "Leave record updated successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# #分割
# 取得所有分割記錄
@app.route("/divide_records", methods=["GET"])
def get_divide_records():
    try:
        office_id = request.args.get("office_id")
        query = """
            SELECT 
                divide_id, 
                divide_records.employee_id AS employee_id, 
                start_time AS start, 
                end_time AS end, 
                created_at, 
                employees.name AS name, 
                divide_records.location_num, 
                divide_records.land_num 
            FROM divide_records
            JOIN employees ON divide_records.employee_id = employees.employee_id
        """
        params = []
        if office_id:
            query += " WHERE employees.office_id = %s"
            params.append(office_id)

        records = execute_query(query, params)
        return jsonify(records), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/divide_records", methods=["POST"])
def add_divide_record():
    data = request.get_json()
    # 必填欄位： employee_id, start_time, end_time, location_num, land_num
    required_fields = ["employee_id", "start_time", "end_time", "location_num", "land_num"]
    if not data or any(field not in data for field in required_fields):
        return jsonify({"error": "Missing required field(s)"}), 400

    employee_id = data.get("employee_id")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    location_num = data.get("location_num")
    land_num = data.get("land_num")

    # 新增分割記錄
    query = """
        INSERT INTO divide_records (employee_id, start_time, end_time, location_num, land_num)
        VALUES (%s, %s, %s, %s, %s)
    """
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor()
    try:
        cursor.execute(query, (employee_id, start_time, end_time, location_num, land_num))
        connection.commit()
        divide_id = cursor.lastrowid
    except mysql.connector.Error as e:
        connection.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    finally:
        cursor.close()
        connection.close()

    # 計算工時差（以小時為單位），假設時間格式為 ISO 格式字串
    start = datetime.fromisoformat(start_time.rstrip("Z"))
    end = datetime.fromisoformat(end_time.rstrip("Z"))
    hours_diff = (end - start).total_seconds() / 3600

    # 更新員工總工時：累加差值
    update_query = "UPDATE employees SET work_hours = work_hours + %s WHERE employee_id = %s"
    execute_query(update_query, [hours_diff, employee_id], fetch=False)

    # 更新月工時：根據排班開始時間的年份與月份更新
    schedule_dt = datetime.fromisoformat(start_time.rstrip("Z"))
    year = schedule_dt.year
    month = schedule_dt.month
    select_query = "SELECT * FROM monthly_work_hours WHERE employee_id = %s AND year = %s AND month = %s"
    monthly_records = execute_query(select_query, [employee_id, year, month])
    if monthly_records and len(monthly_records) > 0:
        update_monthly_query = "UPDATE monthly_work_hours SET work_hours = work_hours + %s WHERE employee_id = %s AND year = %s AND month = %s"
        execute_query(update_monthly_query, [hours_diff, employee_id, year, month], fetch=False)
    else:
        insert_monthly_query = "INSERT INTO monthly_work_hours (employee_id, year, month, work_hours) VALUES (%s, %s, %s, %s)"
        execute_query(insert_monthly_query, [employee_id, year, month, hours_diff], fetch=False)

    new_record = {
        "divide_id": divide_id,
        "employee_id": employee_id,
        "start_time": start_time,
        "end_time": end_time,
        "location_num": location_num,
        "land_num": land_num,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    return jsonify(new_record), 201

@app.route("/divide_records/<int:divide_id>", methods=["DELETE"])
def delete_divide_record(divide_id):
    # 先查詢要刪除的分割記錄資訊
    select_query = "SELECT employee_id, start_time, end_time FROM divide_records WHERE divide_id = %s"
    record = execute_query(select_query, (divide_id,))
    if not record or len(record) == 0:
        return jsonify({"error": "Divide record not found"}), 404

    record = record[0]
    employee_id = record["employee_id"]
    start_time = record["start_time"]
    end_time = record["end_time"]

    # 刪除分割記錄
    delete_query = "DELETE FROM divide_records WHERE divide_id = %s"
    try:
        execute_query(delete_query, (divide_id,), fetch=False)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # 計算工時差
    start = datetime.fromisoformat(start_time.rstrip("Z"))
    end = datetime.fromisoformat(end_time.rstrip("Z"))
    hours_diff = (end - start).total_seconds() / 3600.0
    print("Hours difference:", hours_diff)

    # 更新員工總工時：扣除該時段工時，並確保不小於 0
    update_emp_query = "UPDATE employees SET work_hours = GREATEST(work_hours - %s, 0) WHERE employee_id = %s"
    execute_query(update_emp_query, [hours_diff, employee_id], fetch=False)

    # 更新月工時：以該分割記錄的開始時間所屬的年份與月份扣除該工時差
    schedule_dt = datetime.fromisoformat(start_time.rstrip("Z"))
    year = schedule_dt.year
    month = schedule_dt.month
    update_monthly_query = """
        UPDATE monthly_work_hours 
        SET work_hours = GREATEST(work_hours - %s, 0)
        WHERE employee_id = %s AND year = %s AND month = %s
    """
    execute_query(update_monthly_query, [hours_diff, employee_id, year, month], fetch=False)

    return jsonify({"message": "Divide record deleted successfully"}), 200


# 更新請假記錄（依 divide_id 更新）
@app.route("/divide_records/<int:divide_id>", methods=["PUT"])
def update_divide_record(divide_id):
    data = request.get_json()
    # 允許更新的欄位
    updatable_fields = ["employee_id", "start_time", "end_time", "location_num", "land_num"]
    update_fragments = []
    params = []
    for field in updatable_fields:
        if field in data:
            update_fragments.append(f"{field} = %s")
            params.append(data[field])
    if not update_fragments:
        return jsonify({"error": "No fields to update"}), 400
    params.append(divide_id)
    query = f"UPDATE divide_records SET {', '.join(update_fragments)} WHERE divide_id = %s"
    try:
        execute_query(query, params, fetch=False)
        return jsonify({"message": "Divide record updated successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
#分割

# # Load the CSV files
# FilePath = '/Users/huannn/Desktop/cityproject/flask/data'
# allFileList = os.listdir(FilePath)
# data = pd.DataFrame()

# for file in allFileList:
#     file_path = os.path.join(FilePath,file)
#     if os.path.isfile(file_path):
#         temp = pd.read_csv(file_path, encoding='utf-8')
#         data = pd.concat([data, temp],ignore_index=True)
#     else:
#         print('not a file')

#   ##把年月分開
# data['年月'] = data['年月'].apply(lambda x: f'{x:.2f}')
# temp = pd.DataFrame(data['年月'].str.split(".").tolist(),columns = ['年','月'])
# temp = temp.astype(int)
# data = pd.concat([temp, data],axis=1).drop('年月', axis=1)

# data['行政區'] = data['行政區'].str.replace(' ','')
# data.fillna(0, inplace=True)
@app.route("/employees", methods=["GET"])
def get_employees():
    """
    取得所有員工資料，回傳 employee_id, name, work_hours, work, office_id
    若傳入 office_id，則僅回傳該事務所的員工資料
    """
    try:
        office_id = request.args.get("office_id")
        if office_id:
            query = "SELECT employee_id, name, work_hours, work, office_id FROM employees WHERE office_id = %s"
            employees = execute_query(query, (office_id,))
        else:
            employees = get_employees_from_db()
        return jsonify(employees), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/employees", methods=["POST"])
def add_employee():
    data = request.get_json()
    if not data or "name" not in data:
        return jsonify({"error": "Missing required field: name"}), 400

    # 讀取前端傳入的資料，若未提供 work 或 work_hours 則預設為 1 與 0
    name = data.get("name")
    work = data.get("work", 1)
    work_hours = data.get("work_hours", 0)
    office_id = data.get("office_id")
    if office_id is None:
        return jsonify({"error": "Missing required field: office_id"}), 400

    # 建立 INSERT 語句，增加 office_id 欄位
    query = "INSERT INTO employees (name, work, work_hours, office_id) VALUES (%s, %s, %s, %s)"
    
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor()
    try:
        cursor.execute(query, (name, work, work_hours, office_id))
        connection.commit()
        employee_id = cursor.lastrowid
    except mysql.connector.Error as e:
        connection.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    finally:
        cursor.close()
        connection.close()
    
    new_employee = {
        "employee_id": employee_id,
        "name": name,
        "work": work,
        "work_hours": work_hours,
        "office_id": office_id
    }
    return jsonify(new_employee), 201

@app.route("/employees/<int:employee_id>", methods=["PUT"])
def update_employee(employee_id):
    data = request.get_json()
    # 允許更新的欄位
    allowed_fields = ["name", "work", "work_hours"]
    update_fields = []
    params = []
    for field in allowed_fields:
        if field in data:
            update_fields.append(f"{field} = %s")
            params.append(data[field])
    if not update_fields:
        return jsonify({"error": "No fields to update"}), 400

    params.append(employee_id)
    query = f"UPDATE employees SET {', '.join(update_fields)} WHERE employee_id = %s"
    
    connection = mysql.connector.connect(**db_config)
    cursor = connection.cursor()
    try:
        cursor.execute(query, params)
        connection.commit()
    except mysql.connector.Error as e:
        connection.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    finally:
        cursor.close()
        connection.close()

    return jsonify({"message": "Employee updated successfully"}), 200

@app.route("/offices", methods=["GET"])
def get_offices():
    try:
        query = "SELECT office_id, name FROM offices"
        offices = execute_query(query)
        return jsonify(offices), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


#事務所經緯度資料
offices = {
    "臺南地政事務所": {"lon": 120.203, "lat": 22.991},
    "安南地政事務所": {"lon": 120.197, "lat": 23.047},
    "東南地政事務所": {"lon": 120.219, "lat": 22.979},
    "鹽水地政事務所": {"lon": 120.267, "lat": 23.317},
    "白河地政事務所": {"lon": 120.415, "lat": 23.355},
    "麻豆地政事務所": {"lon": 120.248, "lat": 23.179},
    "新化地政事務所": {"lon": 120.309, "lat": 23.034},
    "佳里地政事務所": {"lon": 120.174, "lat": 23.165},
    "歸仁地政事務所": {"lon": 120.293, "lat": 22.950},
    "玉井地政事務所": {"lon": 120.464, "lat": 23.125},
    "永康地政事務所": {"lon": 120.259, "lat": 23.038},
}


#------------------------------------------------------------------#

#政府公開資料查詢經緯度

csv_path = './109年度臺南市宗地地號屬性資料_合併.csv'
df = pd.read_csv(csv_path)

def get_lat_lng(adm_num,land_num): #adm_num , land_num皆為str
  find = df[(df["地段碼"].astype(str) == str(adm_num)) & (df["地號"] == str(land_num))]

  if not find.empty:
    lat = find["Latitude"].values[0]
    lng = find["Longitude"].values[0]
    return lat,lng
  else:
    try:
      find = df[(df["地段碼"].astype(str) == str(adm_num)) & (df["地號"] == str(land_num).split('-')[0])]
      print("aa",str(land_num).split('-')[0])
      lat = find["Latitude"].values[0]
      lng = find["Longitude"].values[0]
    except Exception as error:
      print('Caught this error: ' + repr(error))
      return None,None
    return lat,lng

print(get_lat_lng('5311','466-4'))
print(get_lat_lng('8019','4-10'))

#計算距離
#OpenRouteService API


api_key = '5b3ce3597851110001cf62483347c4d6b92f4c2e9aaf0126e653211f'
def calculate_distance_ors(api_key, coords):
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": coords
    }
    response = requests.post(url, headers=headers, json=body)
    data = response.json()

    if "routes" in data:
        distance = data['routes'][0]['summary']['distance'] / 1000  # 公里
        duration = data['routes'][0]['summary']['duration'] / 60  # 分鐘
        return distance
    else:
        return None

coords = [[120.2191, 22.9968], [120.2129, 22.9971]]
result = calculate_distance_ors(api_key, coords)

@app.route("/time_predict", methods=["POST"])
def time_predict():

  body = request.json
  print(body)
  
  office = body.get('office')
  adm_num = body.get('adm_num')
  land_num = body.get("land_num")
  area = body.get("area")
  points = body.get("points")
  method = body.get("method")
  category = body.get("category")
  print(adm_num)
  print(land_num)
  """
  office : 事務所(str)
  adm_num : 地段號(str)
  land_num : 地號(str)
  area : 面積
  points : 測釘點數
  method : 圖解法/數值區
  category : 是否地籍整理
  回傳時間為小時
  """
  time=0 #單位 min

  # return 'gg'
  #面積
  area = float(area)
  if area<200:
    time = time+10
  elif area>=200 and area<1000:
    time = time+20
  elif area>=1000 and area<10000:
    time = time+25
  elif area>=10000 and area<20000:
    time = time+50
  elif area>=20000:
    time = time+100
  #釘界
  time = time+5+(points/10)*15

  if method: #圖解法為1數值法為0
    time = time+5

  if category:
    time = time+5

  cor_off = [offices[office]["lon"], offices[office]["lat"]]
  lat , lng = get_lat_lng(adm_num,land_num)
  cor_des = [lng,lat]
  if lat is None: return '0'
  distance = calculate_distance_ors(api_key,[cor_off,cor_des])
  time = time+int(distance)*0.002

  if time<120:
    return '2'
  elif time>=120 and time<210:
    return '4'
  elif time>=210 and time<330:
    return '8'
  elif time>=330:
    return '16'
@app.route('/checkpoint', methods=['GET'])
def checkpoint():
    current_time = datetime.utcnow().isoformat() + 'Z'
    return jsonify({
        'status': 'ok',
        'message': 'Checkpoint reached successfully!',
        'timestamp': current_time
    })
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080)