# === BASE SYSTEM === ======================================
.PHONY: oled_controller
oled_controller:
	./oled_controller.py

.PHONY: fitebox_manager
fitebox_manager:
	./fitebox_manager.py

.PHONY: fitebox_web
fitebox_web:
	make -C web web

.PHONY: fitebox_display
fitebox_display:
	./fitebox_display.py

# === RECORDING === ========================================

.PHONY: recording_start
recording_start:
	-make screen_recording_start
	./recording_engine.sh || make screen_failure

.PHONY: recording_stop
recording_stop:
	-make screen_recording_stop
	-pkill -SIGTERM ffmpeg
	-sleep 2
	-make screen_ready
	-make screen


# === SCREEN MANAGEMENT === ================================

.PHONY: screen_%
screen_%:
	/usr/bin/python3 /app/fitebox_display.py $*

.PHONY: screen
screen:
	/usr/bin/python3 /app/fitebox_display.py --text ""

.PHONY: convert_background_1080p
convert_background_1080p:
	ffmpeg -i background.png -vf scale=1920:1080 background_1080p.png

# .PHONY: plymouth_restart
# plymouth_restart:
# 	-plymouth quit
# 	sleep 1
# 	plymouthd --mode=boot --attach-to-session --pid-file=/tmp/plymouth.pid
# 	sleep 1
# 	plymouth --show-splash

# === OTHERS === ===========================================

.PHONY: supervisor_start
supervisor_start:
	supervisorctl -c /etc/supervisor/supervisord.conf start all

.PHONY: supervisor_stop
supervisor_stop:
	supervisorctl -c /etc/supervisor/supervisord.conf stop all

# .PHONY: uptime
# uptime:
# 	python3 buttons_controller.py uptime )
#
# .PHONY: reboot
# reboot:
# 	python3 buttons_controller.py reboot )
#
#
# .PHONY: shutdown
# shutdown:
# 	-### Shutdown recording ... ###
# 	python3 buttons_controller.py shutdown )
