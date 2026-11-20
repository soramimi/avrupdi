
NAME := myapp
PROJDIR := .
PORT ?= /dev/ttyUSB0
#PART ?= AVR64DU32
PART ?= attiny202
HEX ?= example/avr64dd28blink.hex
ERASE_OPTIONS ?= --allow-eeprom-erase

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
	#avrdude -c serialupdi -P /dev/ttyUSB0 -p avr64du32 -v
	#avrdude -c serialupdi -P /dev/ttyUSB0 -p avr64dd28 -v

.PHONY: query chiperase writehex
query: $(NAME)
	./$(NAME) -p "$(PORT)" -t $(PART) --read-fuses -v

chiperase: $(NAME)
	./$(NAME) -p "$(PORT)" -t $(PART) --chip-erase $(ERASE_OPTIONS)

writehex: $(NAME)
	@test -n "$(HEX)" || { echo "Specify HEX=firmware.hex for $(PART)" >&2; exit 1; }
	./$(NAME) -p "$(PORT)" -t $(PART) $(ERASE_OPTIONS) --write-hex "$(HEX)" $(if $(filter AVR64DU32 avr64du32,$(PART)),--allow-bootrow-erase,)
