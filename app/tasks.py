from app import db, scheduler # Import scheduler instance
from app.models.subscription_config import SubscriptionConfig
from app.models.user import User
from datetime import datetime, time
import logging
import json # For JSON logging
import os   # For path manipulation

# Configure logging for tasks
task_logger = logging.getLogger(__name__)
task_logger.setLevel(logging.INFO)
# Example: Add a handler if not configured globally
if not task_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    task_logger.addHandler(handler)

def distribute_subscription_points_job(app):
    """Scheduled job to distribute points based on active subscription configurations."""
    with app.app_context():
        task_logger.info(f"Running distribute_subscription_points_job at {datetime.utcnow()} UTC")
        active_configs = SubscriptionConfig.query.filter_by(is_active=True).all()
        now_utc = datetime.utcnow()

        # Define log file path
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        log_dir = os.path.join(project_root, 'logs')
        log_file_path = os.path.join(log_dir, 'points_distribution.json')

        # Ensure log directory exists
        if not os.path.exists(log_dir):
            try:
                os.makedirs(log_dir)
                task_logger.info(f"Created log directory: {log_dir}")
            except OSError as e:
                task_logger.error(f"Error creating log directory {log_dir}: {e}")

        new_log_entries = []

        for config in active_configs:
            task_logger.debug(f"Processing config ID: {config.id}, Name: {config.name}")
            if not config.distribution_time:
                task_logger.warning(f"Config ID: {config.id} has no distribution time set. Skipping.")
                continue

            # 1. Check if it's the right minute of the day for this config
            if not (now_utc.hour == config.distribution_time.hour and \
                    now_utc.minute == config.distribution_time.minute):
                task_logger.debug(f"Config ID: {config.id} - Time mismatch.")
                continue

            task_logger.info(f"Config ID: {config.id} - Time match. Proceeding with day check.")

            # 2. Check if it's the right day based on frequency
            day_match = False
            if config.distribution_frequency == 'daily':
                day_match = True
            elif config.distribution_frequency == 'weekly':
                if config.distribution_day is not None and now_utc.weekday() == config.distribution_day:
                    day_match = True
            elif config.distribution_frequency == 'monthly':
                if config.distribution_day is not None and now_utc.day == config.distribution_day:
                    day_match = True

            if not day_match:
                task_logger.debug(f"Config ID: {config.id} - Day mismatch. Freq: {config.distribution_frequency}, Day: {config.distribution_day}, Current weekday/day: {now_utc.weekday()}/{now_utc.day}")
                continue

            task_logger.info(f"Config ID: {config.id} - Day match. Proceeding with last processed check.")

            # 3. Check if already processed for this specific slot today
            if config.last_processed_at:
                if (config.last_processed_at.year == now_utc.year and \
                    config.last_processed_at.month == now_utc.month and \
                    config.last_processed_at.day == now_utc.day and \
                    config.last_processed_at.hour == config.distribution_time.hour and \
                    config.last_processed_at.minute == config.distribution_time.minute):
                    task_logger.info(f"Config ID: {config.id} already processed at {config.last_processed_at.strftime('%Y-%m-%d %H:%M')} for this slot. Skipping.")
                    continue

            task_logger.info(f"Config ID: {config.id} - PASSED ALL CHECKS. Distributing {config.points_to_distribute} points.")

            # 4. Distribute points
            points_distributed_to_users = 0
            if not config.target_groups:
                task_logger.warning(f"Config ID: {config.id} has no target groups. Nothing to distribute.")
            else:
                for group in config.target_groups:
                    task_logger.debug(f"  Distributing to group: {group.name} (ID: {group.id}) for config ID: {config.id}")
                    users_in_group = group.users.all()
                    if not users_in_group:
                        task_logger.debug(f"    Group {group.name} (ID: {group.id}) has no users.")
                        continue
                    for user in users_in_group:
                        balance_before = user.points or 0
                        user.points = balance_before + config.points_to_distribute
                        db.session.add(user)
                        points_distributed_to_users += 1
                        task_logger.debug(f"    Granted {config.points_to_distribute} points to User ID: {user.id} ({user.username}). New balance: {user.points}")

                        # Create log entry
                        log_entry = {
                            "timestamp": now_utc.isoformat() + "Z",
                            "subscription_config_id": config.id,
                            "subscription_config_name": config.name,
                            "target_group_id": group.id,
                            "target_group_name": group.name,
                            "user_id": user.id,
                            "username": user.username,
                            "points_distributed": config.points_to_distribute,
                            "balance_before": balance_before,
                            "balance_after": user.points
                        }
                        new_log_entries.append(log_entry)

            config.last_processed_at = now_utc
            db.session.add(config)
            task_logger.info(f"Config ID: {config.id} - Successfully distributed points to {points_distributed_to_users} user instances. Updated last_processed_at to {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")

        try:
            db.session.commit()
            task_logger.info("Finished distribute_subscription_points_job. Changes committed.")

            # Write logs after successful commit
            if new_log_entries:
                try:
                    all_log_entries = []
                    if os.path.exists(log_file_path):
                        with open(log_file_path, 'r', encoding='utf-8') as f_read:
                            try:
                                all_log_entries = json.load(f_read)
                                if not isinstance(all_log_entries, list):
                                    task_logger.warning(f"Log file {log_file_path} does not contain a valid JSON list. Initializing fresh log.")
                                    all_log_entries = []
                            except json.JSONDecodeError:
                                task_logger.warning(f"Log file {log_file_path} is not valid JSON. Initializing fresh log.")
                                all_log_entries = []

                    all_log_entries.extend(new_log_entries)

                    with open(log_file_path, 'w', encoding='utf-8') as f_write:
                        json.dump(all_log_entries, f_write, indent=4, ensure_ascii=False)
                    task_logger.info(f"Successfully wrote {len(new_log_entries)} new entries to {log_file_path}")
                except IOError as e:
                    task_logger.error(f"IOError writing to log file {log_file_path}: {e}")
                except Exception as e:
                    task_logger.error(f"Unexpected error writing to log file {log_file_path}: {e}", exc_info=True)

        except Exception as e:
            db.session.rollback()
            task_logger.error(f"Error committing changes in distribute_subscription_points_job: {e}", exc_info=True)

def initialize_scheduler(app, scheduler_instance):
    """Adds jobs to the scheduler. Ensures app context is available."""
    if not scheduler_instance.get_job('distribute_points_job'):
        scheduler_instance.add_job(
            id='distribute_points_job', 
            func=distribute_subscription_points_job, 
            args=[app], # Pass the app instance here
            trigger='interval', 
            minutes=1,
            misfire_grace_time=60 # Allow job to run if it was missed by up to 60s
        )
        task_logger.info("Scheduled 'distribute_points_job' to run every 1 minute.")
    else:
        task_logger.info("'distribute_points_job' is already scheduled.") 