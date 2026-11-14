
NAME := myapp
PROJDIR := .

SRCS := main.cpp
LIBS := -pthread

CC := gcc
CXX := g++
LD := $(CXX)
INCLUDEPATH :=
CFLAGS := -O3 $(INCLUDEPATH)
CXXFLAGS := -O3 -std=c++17 -Wall -Wextra -Wpedantic -pthread $(INCLUDEPATH)

OBJS := $(SRCS:%.c=%.o)
OBJS := $(OBJS:%.cpp=%.o)
OBJS := $(OBJS:%.cc=%.o)
DEPS := $(SRCS:%.c=%.d)
DEPS := $(DEPS:%.cpp=%.d)
DEPS := $(DEPS:%.cc=%.d)

all: $(NAME)

$(NAME): $(OBJS)
	$(LD) $(OBJS) -o $(NAME) $(LIBS)

.c.o:
	$(CC) $(CFLAGS) -MMD -MP -MF $(<:%.c=%.d) -c $< -o $(<:%.c=%.o)

.cc.o:
	$(CXX) $(CXXFLAGS) -MMD -MP -MF $(<:%.cc=%.d) -c $< -o $(<:%.cc=%.o)

.cpp.o:
	$(CXX) $(CXXFLAGS) -MMD -MP -MF $(<:%.cpp=%.d) -c $< -o $(<:%.cpp=%.o)

.PHONY: clean
clean:
	-rm $(NAME)
	find $(PROJDIR) -name "*.o" -exec rm {} \;
	find $(PROJDIR) -name "*.d" -exec rm {} \;

.PHONY: run
run:
	./$(NAME)

.PHONY: test
test: $(NAME)
	python3 -m unittest discover -s tests -v

.PHONY: install
install:
	install -m 755 $(NAME) ~/.local/bin/

.PHONY: uninstall
uninstall:
	rm ~/.local/bin/$(NAME)

-include $(DEPS)

dude:
	avrdude -c serialupdi -P /dev/ttyUSB0 -p attiny202 -v

query:
	./myapp /dev/ttyUSB0 -v

chiperase:
	./myapp /dev/ttyUSB0 --chip-erase --allow-eeprom-erase

writehex:
	./myapp /dev/ttyUSB0 --allow-eeprom-erase --write-hex example/attiny202blink.hex
