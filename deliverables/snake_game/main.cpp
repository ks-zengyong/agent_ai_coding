#include <iostream>
#include <conio.h>
#include <windows.h>
#include <vector>
#include <deque>
#include <cstdlib>
#include <ctime>
#include <string>

using namespace std;

// ============================================================
// Console utilities
// ============================================================
void gotoxy(int x, int y) {
    COORD coord;
    coord.X = x;
    coord.Y = y;
    SetConsoleCursorPosition(GetStdHandle(STD_OUTPUT_HANDLE), coord);
}

void hideCursor() {
    CONSOLE_CURSOR_INFO cursorInfo;
    GetConsoleCursorInfo(GetStdHandle(STD_OUTPUT_HANDLE), &cursorInfo);
    cursorInfo.bVisible = false;
    SetConsoleCursorInfo(GetStdHandle(STD_OUTPUT_HANDLE), &cursorInfo);
}

void setConsoleColor(int color) {
    SetConsoleTextAttribute(GetStdHandle(STD_OUTPUT_HANDLE), color);
}

// ============================================================
// Game constants
// ============================================================
const int BOARD_WIDTH = 40;
const int BOARD_HEIGHT = 20;
const int INITIAL_SPEED = 120;  // ms per frame

enum Direction { UP, DOWN, LEFT, RIGHT };
enum CellType { EMPTY, WALL, SNAKE_BODY, FOOD };

// ============================================================
// Point structure
// ============================================================
struct Point {
    int x, y;
    Point(int x = 0, int y = 0) : x(x), y(y) {}
    bool operator==(const Point& other) const {
        return x == other.x && y == other.y;
    }
};

// ============================================================
// Snake class
// ============================================================
class Snake {
public:
    deque<Point> body;
    Direction dir;
    Direction nextDir;

    Snake(int startX, int startY) {
        dir = RIGHT;
        nextDir = RIGHT;
        // Start with 3 segments
        body.push_back(Point(startX, startY));
        body.push_back(Point(startX - 1, startY));
        body.push_back(Point(startX - 2, startY));
    }

    Point head() const { return body.front(); }

    void setDirection(Direction d) {
        // Prevent reversing
        if ((dir == UP && d == DOWN) ||
            (dir == DOWN && d == UP) ||
            (dir == LEFT && d == RIGHT) ||
            (dir == RIGHT && d == LEFT)) {
            return;
        }
        nextDir = d;
    }

    void move(bool grow) {
        dir = nextDir;
        Point newHead = head();
        switch (dir) {
            case UP:    newHead.y--; break;
            case DOWN:  newHead.y++; break;
            case LEFT:  newHead.x--; break;
            case RIGHT: newHead.x++; break;
        }
        body.push_front(newHead);
        if (!grow) {
            body.pop_back();
        }
    }

    bool collidesWithSelf() const {
        Point h = head();
        for (size_t i = 1; i < body.size(); i++) {
            if (body[i] == h) return true;
        }
        return false;
    }

    bool occupies(int x, int y) const {
        for (const auto& p : body) {
            if (p.x == x && p.y == y) return true;
        }
        return false;
    }
};

// ============================================================
// Game class
// ============================================================
class Game {
private:
    vector<vector<CellType>> board;
    Snake snake;
    Point food;
    int score;
    int highScore;
    int speed;
    bool gameOver;
    bool paused;

public:
    Game()
        : snake(BOARD_WIDTH / 2, BOARD_HEIGHT / 2),
          score(0), highScore(0), speed(INITIAL_SPEED),
          gameOver(false), paused(false)
    {
        board.resize(BOARD_HEIGHT, vector<CellType>(BOARD_WIDTH, EMPTY));
        loadHighScore();
        spawnFood();
    }

    void loadHighScore() {
        // Simple file-based high score
        HANDLE hFile = CreateFileA("snake_highscore.dat", GENERIC_READ,
                                   0, NULL, OPEN_EXISTING,
                                   FILE_ATTRIBUTE_NORMAL, NULL);
        if (hFile != INVALID_HANDLE_VALUE) {
            DWORD read;
            ReadFile(hFile, &highScore, sizeof(highScore), &read, NULL);
            CloseHandle(hFile);
        }
    }

    void saveHighScore() {
        HANDLE hFile = CreateFileA("snake_highscore.dat", GENERIC_WRITE,
                                   0, NULL, CREATE_ALWAYS,
                                   FILE_ATTRIBUTE_NORMAL, NULL);
        if (hFile != INVALID_HANDLE_VALUE) {
            DWORD written;
            WriteFile(hFile, &highScore, sizeof(highScore), &written, NULL);
            CloseHandle(hFile);
        }
    }

    void spawnFood() {
        do {
            food.x = rand() % (BOARD_WIDTH - 2) + 1;
            food.y = rand() % (BOARD_HEIGHT - 2) + 1;
        } while (snake.occupies(food.x, food.y));
    }

    void update() {
        if (gameOver || paused) return;

        // Check if snake will eat food
        Point nextHead = snake.head();
        switch (snake.nextDir) {
            case UP:    nextHead.y--; break;
            case DOWN:  nextHead.y++; break;
            case LEFT:  nextHead.x--; break;
            case RIGHT: nextHead.x++; break;
        }

        bool willEat = (nextHead == food);
        snake.move(willEat);

        Point h = snake.head();

        // Wall collision
        if (h.x <= 0 || h.x >= BOARD_WIDTH - 1 ||
            h.y <= 0 || h.y >= BOARD_HEIGHT - 1) {
            gameOver = true;
            return;
        }

        // Self collision
        if (snake.collidesWithSelf()) {
            gameOver = true;
            return;
        }

        // Eat food
        if (willEat) {
            score += 10;
            if (score > highScore) {
                highScore = score;
                saveHighScore();
            }
            spawnFood();
            // Speed up
            if (speed > 40) {
                speed -= 2;
            }
        }
    }

    void draw() {
        // Build frame in a string for flicker-free rendering
        string frame;
        frame.reserve((BOARD_WIDTH + 2) * (BOARD_HEIGHT + 2));

        // Top border
        setConsoleColor(0x08); // Dark gray
        frame += char(201); // ╔
        for (int i = 0; i < BOARD_WIDTH; i++) frame += char(205); // ═
        frame += char(187); // ╗
        frame += '\n';

        for (int y = 0; y < BOARD_HEIGHT; y++) {
            frame += char(186); // ║
            for (int x = 0; x < BOARD_WIDTH; x++) {
                if (x == 0 || x == BOARD_WIDTH - 1 ||
                    y == 0 || y == BOARD_HEIGHT - 1) {
                    frame += char(176); // ░ wall
                }
                else if (snake.occupies(x, y)) {
                    if (snake.head() == Point(x, y)) {
                        frame += 'O'; // head
                    } else {
                        frame += 'o'; // body
                    }
                }
                else if (food.x == x && food.y == y) {
                    frame += '*'; // food
                }
                else {
                    frame += ' ';
                }
            }
            frame += char(186); // ║
            frame += '\n';
        }

        // Bottom border
        frame += char(200); // ╚
        for (int i = 0; i < BOARD_WIDTH; i++) frame += char(205); // ═
        frame += char(188); // ╝
        frame += '\n';

        // Score info
        frame += "  Score: " + to_string(score);
        frame += "  |  High Score: " + to_string(highScore);
        frame += "  |  Speed: " + to_string(INITIAL_SPEED - speed + 1);
        if (paused) {
            frame += "  |  [PAUSED]";
        }
        frame += "\n";
        frame += "  Arrow Keys: Move  |  P: Pause  |  Q: Quit  |  R: Restart\n";

        gotoxy(0, 0);
        cout << frame;
    }

    void handleInput() {
        if (_kbhit()) {
            int key = _getch();
            if (key == 224) { // Arrow keys
                key = _getch();
                switch (key) {
                    case 72: snake.setDirection(UP); break;
                    case 80: snake.setDirection(DOWN); break;
                    case 75: snake.setDirection(LEFT); break;
                    case 77: snake.setDirection(RIGHT); break;
                }
            } else {
                switch (key) {
                    case 'w': case 'W': snake.setDirection(UP); break;
                    case 's': case 'S': snake.setDirection(DOWN); break;
                    case 'a': case 'A': snake.setDirection(LEFT); break;
                    case 'd': case 'D': snake.setDirection(RIGHT); break;
                    case 'p': case 'P': paused = !paused; break;
                    case 'q': case 'Q': gameOver = true; break;
                    case 'r': case 'R': restart(); break;
                }
            }
        }
    }

    void restart() {
        snake = Snake(BOARD_WIDTH / 2, BOARD_HEIGHT / 2);
        score = 0;
        speed = INITIAL_SPEED;
        gameOver = false;
        paused = false;
        spawnFood();
    }

    void run() {
        hideCursor();
        system("cls");

        // Set console title
        SetConsoleTitleA("Snake Game - C++");

        // Resize console window to fit the game
        HWND console = GetConsoleWindow();
        RECT r;
        GetWindowRect(console, &r);
        MoveWindow(console, r.left, r.top, 700, 550, TRUE);

        while (!gameOver) {
            handleInput();
            update();
            draw();
            Sleep(speed);
        }

        // Game over screen
        gotoxy(0, BOARD_HEIGHT + 4);
        setConsoleColor(0x0C); // Red
        cout << "\n";
        cout << "  ================================\n";
        cout << "           G A M E   O V E R      \n";
        cout << "  ================================\n";
        cout << "  Final Score: " << score << "\n";
        cout << "  High Score:  " << highScore << "\n\n";
        cout << "  Press R to restart or Q to quit.\n";

        setConsoleColor(0x07); // Reset color

        // Wait for restart or quit
        while (true) {
            if (_kbhit()) {
                int key = _getch();
                if (key == 'r' || key == 'R') {
                    restart();
                    system("cls");
                    run();
                    return;
                }
                if (key == 'q' || key == 'Q') {
                    break;
                }
            }
            Sleep(50);
        }
    }
};

// ============================================================
// Main
// ============================================================
int main() {
    srand((unsigned int)time(NULL));

    // Set console to UTF-8 for box-drawing characters
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);

    Game game;
    game.run();

    // Restore cursor and colors
    setConsoleColor(0x07);
    system("cls");

    cout << "Thanks for playing Snake!\n";
    return 0;
}
