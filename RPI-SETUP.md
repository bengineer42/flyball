# Setting up the Raspberry Pi

## Flash a new sd card with latest Raspberry Pi OS Lite (64-bit) image.

## Set up
using the GUI, the user was set to `pi` with password `password`, and SSH was enabled.

## Enable PWM 
Add dtoverlay=pwm-2chan to /boot/firmware/config.txt. This defaults to GPIO_18 as the pin for PWM0 and GPIO_19 as the pin for PWM1.
Alternatively, you can change GPIO_18 to GPIO_12 and GPIO_19 to GPIO_13 using dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4.
