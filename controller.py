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

    def position(self, cx, cy):
        return [cx, cy]
