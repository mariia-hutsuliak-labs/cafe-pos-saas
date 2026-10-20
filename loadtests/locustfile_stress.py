"""Лаба 8, додаткове завдання 1: стрес-тест (Stress) і раптовий стрибок (Spike).

Це лише форма навантаження (LoadTestShape). Поведінку користувачів бере
loadtests/locustfile.py, тому файли передаються разом через кому:
  locust -f loadtests/locustfile.py,loadtests/locustfile_stress.py --host ... --headless

Режим задає змінна STRESS_MODE:

  step (за замовчуванням) - покрокове збільшення до відмови.
    Кожні STEP_SECONDS секунд додається STEP_USERS користувачів, до MAX_USERS.
    Зростання зупиняється раніше, якщо система майже лягла (понад 50% помилок
    в останні 20 секунд кроку). Так знаходимо «точку зламу» (Break Point).
    Після цього COOLDOWN_SECONDS секунд працює COOLDOWN_USERS користувачів:
    за цей час видно, чи система сама оговталась після перевантаження.

  spike - раптовий пік трафіку.
    BASE_USERS (BASE_SECONDS) -> стрибок до SPIKE_USERS (SPIKE_SECONDS) ->
    знову BASE_USERS (RECOVERY_SECONDS). Після піку дивимось, за скільки
    секунд затримка і помилки повертаються до норми (автоматичне відновлення).

Усі числа можна змінити через змінні оточення (див. нижче).
"""

import os

from locust import LoadTestShape


def env_int(name, default):
    return int(os.getenv(name, default))


MODE = os.getenv("STRESS_MODE", "step")

# step
STEP_USERS = env_int("STEP_USERS", 100)
STEP_SECONDS = env_int("STEP_SECONDS", 60)
MAX_USERS = env_int("MAX_USERS", 1500)
STEP_SPAWN_RATE = env_int("STEP_SPAWN_RATE", 50)
COOLDOWN_USERS = env_int("COOLDOWN_USERS", 150)
COOLDOWN_SECONDS = env_int("COOLDOWN_SECONDS", 180)
DEAD_ERROR_RATIO = 0.5

# spike
BASE_USERS = env_int("BASE_USERS", 150)
SPIKE_USERS = env_int("SPIKE_USERS", 900)
BASE_SECONDS = env_int("BASE_SECONDS", 120)
SPIKE_SECONDS = env_int("SPIKE_SECONDS", 60)
RECOVERY_SECONDS = env_int("RECOVERY_SECONDS", 240)
SPIKE_SPAWN_RATE = env_int("SPIKE_SPAWN_RATE", 100)


class StressShape(LoadTestShape):
    cooldown_start = None

    def tick(self):
        run_time = self.get_run_time()
        if MODE == "spike":
            return self.spike(run_time)
        return self.step(run_time)

    def step(self, run_time):
        if self.cooldown_start is None:
            step = int(run_time // STEP_SECONDS)
            users = (step + 1) * STEP_USERS
            into_step = run_time - step * STEP_SECONDS
            # В кінці кроку дивимось, чи система ще жива.
            dead = into_step > STEP_SECONDS - 20 and self.system_is_dead()
            if users <= MAX_USERS and not dead:
                return users, STEP_SPAWN_RATE
            self.cooldown_start = run_time
        if run_time < self.cooldown_start + COOLDOWN_SECONDS:
            return COOLDOWN_USERS, STEP_SPAWN_RATE
        return None

    def spike(self, run_time):
        if run_time < BASE_SECONDS:
            return BASE_USERS, SPIKE_SPAWN_RATE
        if run_time < BASE_SECONDS + SPIKE_SECONDS:
            return SPIKE_USERS, SPIKE_SPAWN_RATE
        if run_time < BASE_SECONDS + SPIKE_SECONDS + RECOVERY_SECONDS:
            return BASE_USERS, SPIKE_SPAWN_RATE
        return None

    def system_is_dead(self):
        total = self.runner.stats.total
        rps = total.current_rps
        return rps > 0 and total.current_fail_per_sec / rps > DEAD_ERROR_RATIO
