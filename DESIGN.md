# HumCtrl Design

The system hardware comes in two main parts sensing and actuation. For this system these parts can be change independently depending on the requirements and constraints of the application.

For the sensor part of the system, the main requirement is to be able to read the absolute humidity of the air so along as the sensor can do this and feed it into the control system it can be used.

## Actuation

The actuation part of the system is much more variable in how it can work, it could be set up in a number of different ways using pumps and valves or could use a dehumidifier and humidifier. Although these are very diffrent systems it comes down to the same thing, controlling the ratio of wetting and drying of the system.

For this project we are going to focus on using two pumps, one pumping wet air and one dry.

## Pump control

In a two pump system, when given time to equalise and assuming the temperature is constant, the (absolute) humidity will equal:
$$H = \frac{Q_\mathrm{wet} H_\mathrm{wet} + Q_\mathrm{dry} H_\mathrm{dry}}{Q_\mathrm{wet} + Q_\mathrm{dry}}$$

Where H is the overall humidity, Q<sub>wet</sub> and Q<sub>dry</sub> are the flow rates of the wet and dry pumps respectively, and H<sub>wet</sub> and H<sub>dry</sub> are the humidity of the wet and dry air respectively.

As H<sub>wet</sub> and H<sub>dry</sub> will be constant the overall humidity will be a function of the flow rates of the two pumps. This can be simplified to:
$$h = \frac{Q_\mathrm{wet} - Q_\mathrm{dry}}{Q_\mathrm{wet} + Q_\mathrm{dry}}$$
$$H = h(H_\mathrm{wet} - H_\mathrm{dry})+ H_\mathrm{dry}$$
Where h is the flow ratio of the two pumps, which will be the main control variable for the system.

There are two main choises when controlling the flow rates of the two pumps, either to have a constant or variable total flow rate and if the pumps are single or variable speed.

Assuming the max possible flow rate for a constant flow the flow rate will equal Min(Q<sub>wet</sub><sup>max</sup>, Q<sub>dry</sub><sup>max</sup>) and for a variable flow rate the flow rate will be between Min(Q<sub>wet</sub><sup>max</sup>, Q<sub>dry</sub><sup>max</sup>) and Q<sub>wet</sub><sup>max</sup> + Q<sub>dry</sub><sup>max</sup>.

If the pumps are single speed then the average flow rate will be controlled by the duty cycle of the pumps, and if the pumps are variable speed then the flow rate will be controlled by the speed of the pumps.

For each method there are pros and cons:

- Pump lifetime - Pumps have a limited number of cycles, so repeatedly turning the pump on and off will likely reduce the lifetime of the pump.
- Noise - turning the pump on and off will likely produce more noise than running the pump at a constant speed
- Hardware - PWM control requires a transistor with a high enough switching speed, whereas intermittent control can be done with a relay or transistor.

## Hardware

The hardware setup consists of sensor/s, pumps and the parts needed to interface with these.
The main IO control can be done via a Raspberry Pi Pico or a Raspberry Pi depending on whether the user wants to host the control software on the device or on a separate computer.

### Sensors

For this version for humidity sensing, the SHT45 is used. The reason this was chosen over the previous version DHT22 is because the SHT45 is more accurate and has a faster response time, and communicates via I2C which means that a Raspberry Pi no longer requires a separate Arduino to communicate with the sensors.

The SHT45 can be purchased on a breakout board with a Qwiic/STEMMA QT connector which can connect directly to the Raspberry Pi or Pico I2C pins. If multiple sensors are used, a Qwiic/STEMMA QT multiplex splitter can be used to connect multiple sensors to the same I2C bus.

### Pumps

As mentioned above the actuation can be done in a number of ways and even when just considering pumps there are many different types of pumps that can be used and the control hardware will be depended on the pumps used. This is going to focus on using DC motor pumps as these are economical, ubiquitous and easy to control and change for system requirements. There are some considerations when using DC motor:

- Dead zone - The pump needs a minimum voltage to overcome the load torque, so if the PWM duty cycle is too low the pump will not run.
- Hysteresis - when not running the static friction will need to be overcome to start the pump, so the pump will need a higher voltage to start than to keep running.
- Acceleration - The pump will take some time to accelerate to the target speed so when running intermittently the pump will not be at the target speed for the whole time.

Controlling DC motor is simple generally the speed increases linearly with the voltage applied to the motor. Variable voltage can be simulated by using a PWM signal to turn a higher voltage on and off at a high frequency, the average voltage applied to the motor will be the duty cycle of the PWM signal multiplied by the supply voltage. So a 5V supply with a 50% duty cycle will give an average voltage of 2.5V to the motor.

As the control hardware (RPI or Pico) can turn on and off a GPIO pin the output current and voltage are likely to be too low to drive the pump directly so we need a way of taking a low power output signal or PWM and using it to control a higher power signal to drive the pump. The main electronic component for this is a transistor, which can be used as a switch or an amplifier. (For the single speed pump a relay could be used but we are going to ignore this as it's noisy and they only have a limited number of cycles.) Using a Transistor as a switch for a motor will also require a few other components to protect the transistor and the control hardware such as resistors and a flyback diode, when these systems are put together they are called a motor driver. The main considerations when choosing a motor driver are:

- Motor voltage and current rating - The motor driver must be able to handle the voltage and current of the pump.
- Voltage drop - The motor driver will have a voltage drop across it when it is on, so the voltage applied to the pump will be less than the supply voltage.
- Switching speed - The motor driver must be able to switch fast enough for the PWM signal.
- Control signal voltage - The motor driver must be able to be controlled by the voltage of the control hardware.

For this system we are going to use 6V DC motor pumps with a average current of 0.5A and a peak of about 2A. There are many diffrent motor drivers that can be used for this or even a custom circuit can be made.
