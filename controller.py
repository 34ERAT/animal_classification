import config

class TurretController:
    def compute_pid_speeds(self, cx, cy):
        errorX = cx - config.CAMERA_CENTER_X
        errorY = cy - config.CAMERA_CENTER_Y

        if abs(errorX) < config.DEADBAND:
            errorX = 0
        if abs(errorY) < config.DEADBAND:
            errorY = 0

        pan_speed = config.KP * errorX
        tilt_speed = config.KP * errorY

        return pan_speed, tilt_speed

    def compute_discrete_command(self, cx, cy):
        offset_x = cx - config.CAMERA_CENTER_X
        offset_y = cy - config.CAMERA_CENTER_Y

        # Prioritize whichever axis is more off-center
        if abs(offset_x) >= abs(offset_y) and abs(offset_x) > config.CENTER_TOLERANCE_X:
            direction = "RIGHT" if offset_x > 0 else "LEFT"
            angle = min(config.MAX_ANGLE_STEP, int(abs(offset_x) / config.CAMERA_CENTER_X * config.MAX_ANGLE_STEP) + 1)
        elif abs(offset_y) > config.CENTER_TOLERANCE_Y:
            direction = "DOWN" if offset_y > 0 else "UP"
            angle = min(config.MAX_ANGLE_STEP, int(abs(offset_y) / config.CAMERA_CENTER_Y * config.MAX_ANGLE_STEP) + 1)
        else:
            # Already centered, no movement needed
            direction = "LEFT"
            angle = 0

        return [direction, angle, True]
