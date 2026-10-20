"""Measurement 02."""

import datetime
import multiprocessing

import duckdb


def hold_write(db_path, ready_event):
    """Hold."""
    conn = duckdb.connect(db_path)
    ready_event.set()
    import time

    time.sleep(10)
    conn.close()


def try_read(db_path, result_queue):
    """Try read."""
    try:
        conn = duckdb.connect(db_path, read_only=True)
        conn.close()
        result_queue.put("Success")
    except duckdb.Error as e:
        result_queue.put(f"Failed: {e}")


def main():
    """Main."""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    out = []
    out.append(f"Date: {date_str}")
    out.append("Measurement: One-writer rule")

    db_path = "measurements/test_writer.duckdb"
    # cleanup
    import os

    if os.path.exists(db_path):
        os.remove(db_path)

    manager = multiprocessing.Manager()
    ready_event = manager.Event()
    result_queue = manager.Queue()

    p_writer = multiprocessing.Process(target=hold_write, args=(db_path, ready_event))
    p_writer.start()

    ready_event.wait(5)

    p_reader = multiprocessing.Process(target=try_read, args=(db_path, result_queue))
    p_reader.start()
    p_reader.join()

    res = result_queue.get()
    out.append(f"Result: {res}")

    p_writer.terminate()
    p_writer.join()

    with open("measurements/02_one_writer_rule.md", "w") as f:
        f.write("\n".join(out) + "\n")

    if os.path.exists(db_path):
        os.remove(db_path)


if __name__ == "__main__":
    main()
