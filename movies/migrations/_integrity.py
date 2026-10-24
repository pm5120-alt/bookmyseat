def install_screen_integrity_triggers(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        statements = (
        """CREATE OR REPLACE FUNCTION movies_check_show_screen_match() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM movies_screen WHERE id = NEW.screen_id AND theater_id = NEW.theater_id)
             OR EXISTS (SELECT 1 FROM movies_showseat ss JOIN movies_seat seat ON seat.id = ss.seat_id
                        WHERE ss.show_id = NEW.id AND seat.screen_id <> NEW.screen_id) THEN
            RAISE EXCEPTION 'show screen must match its theater and existing seat inventory' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql""",
        "DROP TRIGGER IF EXISTS movies_show_screen_match ON movies_show",
        "CREATE TRIGGER movies_show_screen_match BEFORE INSERT OR UPDATE OF theater_id, screen_id ON movies_show FOR EACH ROW EXECUTE FUNCTION movies_check_show_screen_match()",
        """CREATE OR REPLACE FUNCTION movies_check_showseat_screen_match() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM movies_show s JOIN movies_seat seat ON seat.id = NEW.seat_id
            JOIN movies_screen sc ON sc.id = seat.screen_id
            WHERE s.id = NEW.show_id AND s.screen_id = seat.screen_id AND s.theater_id = sc.theater_id
          ) THEN
            RAISE EXCEPTION 'show seat must belong to the screen and theater of the show' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql""",
        "DROP TRIGGER IF EXISTS movies_showseat_screen_match ON movies_showseat",
        "CREATE TRIGGER movies_showseat_screen_match BEFORE INSERT OR UPDATE OF show_id, seat_id ON movies_showseat FOR EACH ROW EXECUTE FUNCTION movies_check_showseat_screen_match()",
        """CREATE OR REPLACE FUNCTION movies_check_screen_theater_update() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM movies_show WHERE screen_id = OLD.id AND theater_id <> NEW.theater_id) THEN
            RAISE EXCEPTION 'screen theater cannot change while shows reference it' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql""",
        "DROP TRIGGER IF EXISTS movies_screen_theater_match ON movies_screen",
        "CREATE TRIGGER movies_screen_theater_match BEFORE UPDATE OF theater_id ON movies_screen FOR EACH ROW EXECUTE FUNCTION movies_check_screen_theater_update()",
        """CREATE OR REPLACE FUNCTION movies_check_seat_screen_update() RETURNS trigger AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM movies_showseat ss JOIN movies_show s ON s.id = ss.show_id
                     WHERE ss.seat_id = OLD.id AND s.screen_id <> NEW.screen_id) THEN
            RAISE EXCEPTION 'seat screen cannot change while show seat rows use it' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql""",
        "DROP TRIGGER IF EXISTS movies_seat_screen_match ON movies_seat",
        "CREATE TRIGGER movies_seat_screen_match BEFORE UPDATE OF screen_id ON movies_seat FOR EACH ROW EXECUTE FUNCTION movies_check_seat_screen_update()",
        )
        for statement in statements:
            schema_editor.execute(statement)
    elif vendor == "sqlite":
        for trigger in ("movies_show_screen_match_insert", "movies_show_screen_match_update", "movies_showseat_screen_match_insert",
                        "movies_showseat_screen_match_update", "movies_screen_theater_match", "movies_seat_screen_match"):
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        statements = (
            "CREATE TRIGGER movies_show_screen_match_insert BEFORE INSERT ON movies_show WHEN NOT EXISTS (SELECT 1 FROM movies_screen WHERE id=NEW.screen_id AND theater_id=NEW.theater_id) BEGIN SELECT RAISE(ABORT, 'show screen must belong to its theater'); END",
            "CREATE TRIGGER movies_show_screen_match_update BEFORE UPDATE OF theater_id, screen_id ON movies_show WHEN NOT EXISTS (SELECT 1 FROM movies_screen WHERE id=NEW.screen_id AND theater_id=NEW.theater_id) OR EXISTS (SELECT 1 FROM movies_showseat ss JOIN movies_seat seat ON seat.id=ss.seat_id WHERE ss.show_id=NEW.id AND seat.screen_id<>NEW.screen_id) BEGIN SELECT RAISE(ABORT, 'show screen must match its theater and existing seat inventory'); END",
            "CREATE TRIGGER movies_showseat_screen_match_insert BEFORE INSERT ON movies_showseat WHEN NOT EXISTS (SELECT 1 FROM movies_show s JOIN movies_seat seat ON seat.id=NEW.seat_id JOIN movies_screen sc ON sc.id=seat.screen_id WHERE s.id=NEW.show_id AND s.screen_id=seat.screen_id AND s.theater_id=sc.theater_id) BEGIN SELECT RAISE(ABORT, 'show seat must belong to the screen and theater of the show'); END",
            "CREATE TRIGGER movies_showseat_screen_match_update BEFORE UPDATE OF show_id, seat_id ON movies_showseat WHEN NOT EXISTS (SELECT 1 FROM movies_show s JOIN movies_seat seat ON seat.id=NEW.seat_id JOIN movies_screen sc ON sc.id=seat.screen_id WHERE s.id=NEW.show_id AND s.screen_id=seat.screen_id AND s.theater_id=sc.theater_id) BEGIN SELECT RAISE(ABORT, 'show seat must belong to the screen and theater of the show'); END",
            "CREATE TRIGGER movies_screen_theater_match BEFORE UPDATE OF theater_id ON movies_screen WHEN EXISTS (SELECT 1 FROM movies_show WHERE screen_id=OLD.id AND theater_id<>NEW.theater_id) BEGIN SELECT RAISE(ABORT, 'screen theater cannot change while shows reference it'); END",
            "CREATE TRIGGER movies_seat_screen_match BEFORE UPDATE OF screen_id ON movies_seat WHEN EXISTS (SELECT 1 FROM movies_showseat ss JOIN movies_show s ON s.id=ss.show_id WHERE ss.seat_id=OLD.id AND s.screen_id<>NEW.screen_id) BEGIN SELECT RAISE(ABORT, 'seat screen cannot change while show seat rows use it'); END",
        )
        for statement in statements:
            schema_editor.execute(statement)


def remove_screen_integrity_triggers(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute("DROP TRIGGER IF EXISTS movies_showseat_screen_match ON movies_showseat;")
        schema_editor.execute("DROP TRIGGER IF EXISTS movies_show_screen_match ON movies_show;")
        schema_editor.execute("DROP TRIGGER IF EXISTS movies_screen_theater_match ON movies_screen;")
        schema_editor.execute("DROP TRIGGER IF EXISTS movies_seat_screen_match ON movies_seat;")
        for function in ("movies_check_showseat_screen_match", "movies_check_show_screen_match", "movies_check_screen_theater_update", "movies_check_seat_screen_update"):
            schema_editor.execute(f"DROP FUNCTION IF EXISTS {function}();")
    elif vendor == "sqlite":
        for trigger in ("movies_show_screen_match_insert", "movies_show_screen_match_update", "movies_showseat_screen_match_insert",
                        "movies_showseat_screen_match_update", "movies_screen_theater_match", "movies_seat_screen_match"):
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {trigger}")


def install_booking_confirmation_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        statements = (
        """CREATE OR REPLACE FUNCTION movies_require_verified_booking_confirmation() RETURNS trigger AS $$
        BEGIN
          IF NEW.status = 'confirmed' AND (TG_OP = 'INSERT' OR OLD.status <> 'confirmed') THEN
            IF NOT EXISTS (SELECT 1 FROM movies_payment p WHERE p.booking_id = NEW.id AND p.status = 'captured' AND p.amount = NEW.total_amount) THEN
              RAISE EXCEPTION 'booking confirmation requires a matching captured payment' USING ERRCODE = '23514';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM movies_bookingseat bs WHERE bs.booking_id = NEW.id) OR EXISTS (
              SELECT 1 FROM movies_bookingseat bs JOIN movies_showseat ss ON ss.id = bs.show_seat_id
              WHERE bs.booking_id = NEW.id AND (ss.status <> 'booked' OR ss.active_booking_id <> NEW.id)
            ) THEN
              RAISE EXCEPTION 'booking confirmation requires all seats to be booked for this booking' USING ERRCODE = '23514';
            END IF;
          END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql""",
        "DROP TRIGGER IF EXISTS movies_booking_confirmation_guard ON movies_booking",
        "CREATE TRIGGER movies_booking_confirmation_guard BEFORE INSERT OR UPDATE OF status, total_amount ON movies_booking FOR EACH ROW EXECUTE FUNCTION movies_require_verified_booking_confirmation()",
        )
        for statement in statements:
            schema_editor.execute(statement)
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS movies_booking_confirmation_guard_insert")
        schema_editor.execute("DROP TRIGGER IF EXISTS movies_booking_confirmation_guard_update")
        schema_editor.execute("CREATE TRIGGER movies_booking_confirmation_guard_insert BEFORE INSERT ON movies_booking WHEN NEW.status='confirmed' BEGIN SELECT RAISE(ABORT, 'booking confirmation requires captured payment and booked seats'); END")
        schema_editor.execute("CREATE TRIGGER movies_booking_confirmation_guard_update BEFORE UPDATE OF status, total_amount ON movies_booking WHEN NEW.status='confirmed' AND OLD.status<>'confirmed' AND (NOT EXISTS (SELECT 1 FROM movies_payment p WHERE p.booking_id=NEW.id AND p.status='captured' AND p.amount=NEW.total_amount) OR NOT EXISTS (SELECT 1 FROM movies_bookingseat bs WHERE bs.booking_id=NEW.id) OR EXISTS (SELECT 1 FROM movies_bookingseat bs JOIN movies_showseat ss ON ss.id=bs.show_seat_id WHERE bs.booking_id=NEW.id AND (ss.status<>'booked' OR ss.active_booking_id<>NEW.id))) BEGIN SELECT RAISE(ABORT, 'booking confirmation requires captured payment and booked seats'); END")


def remove_booking_confirmation_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP TRIGGER IF EXISTS movies_booking_confirmation_guard ON movies_booking;")
        schema_editor.execute("DROP FUNCTION IF EXISTS movies_require_verified_booking_confirmation();")
    elif schema_editor.connection.vendor == "sqlite":
        schema_editor.execute("DROP TRIGGER IF EXISTS movies_booking_confirmation_guard_insert")
        schema_editor.execute("DROP TRIGGER IF EXISTS movies_booking_confirmation_guard_update")
